#!/usr/bin/env python3
"""Every engine in the pod is reachable, authenticated, and alone on its port.

With one engine on the card there is one port, one Service port and one path, and
a golden diff is a fine way to check them. With several engines in the one
container that holds the card (SUP-332) there are three agreements no golden can
see, because each side of them renders perfectly on its own:

  1. **The published path is the engine's own `--root-path`.** The Ingress
     forwards the prefix verbatim; the engine strips its `root_path` before
     comparing the path against the prefixes it authenticates. Publish
     `/coder/v1` to an engine started without `--root-path /coder` and the engine
     answers 404 to every request — publish it to an engine whose root path is
     something *else* and it is the same. Both deploy. (That the strip happens at
     all is `charts/tests/root_path_guard.py`.)
  2. **The Ingress backend port is that engine's port.** Two engines differ by
     one digit here, and crossing them serves each model under the other's name
     — with both pods Ready and `/v1/models` disagreeing with the URL.
  3. **Nothing published escapes `/v1`.** vLLM serves `/metrics`, `/docs` and
     `/tokenize` outside its authentication middleware, under the prefix as well
     as without it.

And two things that are true of one engine or ten:

  4. The key is never on a command line. Arguments are rendered into the
     manifests and published in the evidence snapshot in clear (marketplace trap
     4) — including the command lines that moved into `engines.json`.
  5. Exactly one container asks for the card, and every other container asks for
     zero explicitly, because a GPU space's LimitRange defaults a missing
     `nvidia.com/gpu` to the reserved count.

    charts/tests/engine_routing.py model-server-two-engines
"""

from __future__ import annotations

import json
import subprocess
import sys

import yaml

CHART = "swarm-model-server"
# The key every model-server case renders, from cases/model-server-cases.py.
TEST_KEY = "TESTKEYtestkey0123456789abcdefAB"


def render(case: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "ms", f"charts/{CHART}", "--namespace", "model-server",
         "--values", f"charts/tests/cases/{case}.yaml"],
        capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def flag(args: list[str], name: str) -> str | None:
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
    return None


def engines_of(docs: list[dict]) -> list[dict]:
    """Every engine the pod runs, from whichever shape the chart rendered."""
    configmaps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "ConfigMap"}
    shared = next((c for name, c in configmaps.items() if name.endswith("-engines")), None)
    if shared is not None:
        spec = json.loads(shared["data"]["engines.json"])["engines"]
        return [{"name": e["name"], "args": e["command"], "declared": e} for e in spec]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return [{"name": container["name"], "args": container.get("args") or [], "declared": None}]


def main(case: str) -> int:
    docs = render(case)
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    service = next(d for d in docs if d["kind"] == "Service")
    ingress = next((d for d in docs if d["kind"] == "Ingress"), None)
    pod = deployment["spec"]["template"]["spec"]
    engines = engines_of(docs)
    problems: list[str] = []

    # Where each engine actually listens, and under which prefix it will accept a
    # request at all.
    listening: dict[int, dict] = {}
    for engine in engines:
        port = flag(engine["args"], "--port")
        if port is None:
            problems.append(f"{engine['name']} is started without --port")
            continue
        engine["port"] = int(port)
        engine["rootPath"] = flag(engine["args"], "--root-path") or ""
        engine["model"] = flag(engine["args"], "--served-model-name")
        if engine["port"] in listening:
            problems.append(f"{engine['name']} and {listening[engine['port']]['name']} "
                            f"both listen on {engine['port']}")
        listening[engine["port"]] = engine
        if engine["declared"] is not None and engine["declared"]["rootPath"] != engine["rootPath"]:
            problems.append(f"{engine['name']}: engines.json says root path "
                            f"{engine['declared']['rootPath']!r} and the command line says "
                            f"{engine['rootPath']!r}")

    # The container has to expose every port an engine listens on, and the
    # Service has to route to it.
    container = next(c for c in pod["containers"] if c["name"] == "vllm")
    exposed = {p["containerPort"]: p["name"] for p in container["ports"]}
    for engine in engines:
        if engine.get("port") not in exposed:
            problems.append(f"{engine['name']} listens on {engine.get('port')}, "
                            f"which the container does not expose: {sorted(exposed)}")
    service_ports = {p["port"]: p for p in service["spec"]["ports"]}
    routed = {}
    for number, port in service_ports.items():
        target = port["targetPort"]
        container_port = next((cp for cp, name in exposed.items() if name == target), target)
        if container_port not in exposed:
            problems.append(f"the Service routes {number} to {target!r}, which no engine exposes")
            continue
        routed[number] = container_port

    # Every published path belongs to exactly one engine, under that engine's own
    # prefix, and reaches that engine's port.
    if ingress is not None:
        paths = ingress["spec"]["rules"][0]["http"]["paths"]
        published = set()
        for rule in paths:
            path, backend = rule["path"], rule["backend"]["service"]["port"]["number"]
            prefix, _, suffix = path[1:].partition("/")
            engine = next((e for e in engines if e["rootPath"] == ""), None) if not suffix else None
            if suffix:
                engine = next((e for e in engines if e["rootPath"] == f"/{prefix}"), None)
                tail = f"/{suffix}"
            else:
                tail = path
            if engine is None:
                problems.append(f"{path} is published and no engine is started with a matching "
                                f"--root-path: " +
                                ", ".join(f"{e['name']}={e['rootPath']!r}" for e in engines))
                continue
            if not tail.startswith("/v1"):
                problems.append(f"{path} publishes {tail}, which vLLM does not authenticate")
            if routed.get(backend) != engine["port"]:
                problems.append(f"{path} is routed to Service port {backend} -> container "
                                f"{routed.get(backend)}, but {engine['name']} listens on "
                                f"{engine['port']}")
            published.add(engine["name"])
        for engine in engines:
            if engine["name"] not in published:
                problems.append(f"{engine['name']} serves {engine['model']} and nothing "
                                f"publishes it")

    # The key, never on a command line — including the ones in engines.json.
    for engine in engines:
        if any(arg.startswith("--api-key") for arg in engine["args"]):
            problems.append(f"{engine['name']} is given --api-key on its command line")
        if any(TEST_KEY in str(arg) for arg in engine["args"]):
            problems.append(f"{engine['name']} carries the key in an argument")
    for doc in docs:
        if doc["kind"] == "Secret":
            continue
        if TEST_KEY in yaml.safe_dump(doc):
            problems.append(f"the key is written into {doc['kind']}/{doc['metadata']['name']}")

    # One container holds the card; every other one asks for zero in writing.
    holders = []
    for container in pod.get("initContainers", []) + pod["containers"]:
        asked = container["resources"]["requests"].get("nvidia.com/gpu")
        if asked is None:
            problems.append(f"{container['name']} does not say how many GPUs it wants, "
                            f"so a GPU space's LimitRange answers for it")
        elif int(asked) > 0:
            holders.append(container["name"])
    if len(holders) != 1:
        problems.append(f"{len(holders)} containers ask for the card ({holders}): Kubernetes "
                        f"allocates a device to one container, so engines that share it share "
                        f"a container")

    for problem in problems:
        print(f"  FAIL  {case}: {problem}")
    if problems:
        return 1
    print(f"  ok    {case}: {len(engines)} engine(s), "
          + ", ".join(f"{e['name']} on :{e['port']}{e['rootPath']}/v1" for e in engines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
