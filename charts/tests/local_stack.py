#!/usr/bin/env python3
"""
A router deployed with no built-in model runs the API, the console, the
database and the egress — and nothing of the local inference stack.

That is the configuration the listing deploys when its form is left as it
arrives (0.13.0, SUP-245), and it is a property of four charts together, which
no single golden states. So this renders each of the listing's four components
with the values that configuration hands it, and names every container that
would run:

  - no pod from confidential-router-ollama or confidential-router-litellm, and
    nothing from either of them besides its one inert ConfigMap;
  - the API pod carrying both the router and the attested egress, because an
    external endpoint is the only thing this deployment can serve;
  - no GPU requested by anything.

    charts/tests/local_stack.py
"""

import subprocess
import sys

import yaml

# (component, chart, case) — the listing's components, in its order.
COMPONENTS = [
    ("ollama", "confidential-router-ollama", "ollama-no-models"),
    ("litellm", "confidential-router-litellm", "litellm-no-models"),
    ("router-api", "confidential-router-api", "api-external-only"),
    ("router-ui", "confidential-router-ui", "ui-default"),
]

WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet"}

# Every pod that should run, by workload name, with its containers.
EXPECTED = {
    "confidential-router-api": {"router-api", "gatekeeper"},
    "confidential-router-ui": {"router-ui"},
    "confidential-router-postgresql": None,
    "confidential-router-postgresql-etcd": None,
    "confidential-router-postgresql-router": None,
}


def render(chart: str, case: str) -> list[dict]:
    output = subprocess.run(
        ["helm", "template", "cr", f"charts/{chart}", "--namespace", "confidential-router",
         "--values", f"charts/tests/cases/{case}.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(output) if d]


def pod_spec(document: dict) -> dict:
    spec = document.get("spec") or {}
    if document["kind"] == "Pod":
        return spec
    if document["kind"] == "CronJob":
        return spec["jobTemplate"]["spec"]["template"]["spec"]
    return spec["template"]["spec"]


def gpu_requested(pod: dict) -> bool:
    for container in (pod.get("containers") or []) + (pod.get("initContainers") or []):
        for bound in ("requests", "limits"):
            value = ((container.get("resources") or {}).get(bound) or {}).get("nvidia.com/gpu")
            if value not in (None, 0, "0"):
                return True
    return bool(pod.get("runtimeClassName"))


def main() -> int:
    failures = 0
    running: dict[str, set[str]] = {}

    for component, chart, case in COMPONENTS:
        documents = render(chart, case)
        if not documents:
            # The cloud refuses a component with no manifests, so an empty render
            # is a deployment that cannot be created at all.
            print(f"  FAIL  {component} renders nothing, which the cloud refuses")
            failures += 1
        for document in documents:
            name = document["metadata"]["name"]
            if component in ("ollama", "litellm") and (document["kind"], name) != ("ConfigMap", chart):
                print(f"  FAIL  {component} renders {document['kind']}/{name} with no model selected")
                failures += 1
            if document["kind"] not in WORKLOADS:
                continue
            pod = pod_spec(document)
            running[name] = {c["name"] for c in pod.get("containers") or []}
            if gpu_requested(pod):
                print(f"  FAIL  {document['kind']}/{name} asks for a GPU with no model selected")
                failures += 1

    for name in sorted(set(running) - set(EXPECTED)):
        print(f"  FAIL  {name} runs, and is not part of an external-only router")
        failures += 1
    for name in sorted(set(EXPECTED) - set(running)):
        print(f"  FAIL  {name} is not rendered")
        failures += 1
    for name, containers in EXPECTED.items():
        if containers is not None and name in running and running[name] != containers:
            print(f"  FAIL  {name} runs {sorted(running[name])}, expected {sorted(containers)}")
            failures += 1

    if failures:
        return 1
    for name in sorted(running):
        print(f"  ok    {name}: {', '.join(sorted(running[name]))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
