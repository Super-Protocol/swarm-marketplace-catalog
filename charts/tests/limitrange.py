#!/usr/bin/env python3
"""
Every container these charts render is admissible on a cluster space.

A cluster space is created with a LimitRange the cloud writes itself
(`libs/kubernetes/src/lib/namespace-provisioning.service.ts`, under
`clusterPlacement.enforceResourceLimits`, which is on by default):

    type: Container
    min:                  { cpu: 100m, memory: 128Mi }
    maxLimitRequestRatio: { cpu: 1,    memory: 1 }
    default:              50% of the space's max
    defaultRequest:       25% of the space's max

Three rules follow, and a container breaking any of them is refused at admission
— so the pod never exists. That does not present as a quota message anywhere an
operator looks; it presents as "the app is broken" (SUP-238).

    1. cpu and memory, request and limit alike, are at least the floor.
    2. request equals limit, because the ratio is 1.
    3. both are declared. A container that declares neither is given a limit of
       50% and a request of 25% of the space's max, and a ratio of 2 is refused
       by the same LimitRange that filled them in.

Checked against the render rather than against values.yaml: a floor a listing
patches back under is the same outage, and only the render sees that.

    charts/tests/limitrange.py                 # every local case in cases.tsv
    charts/tests/limitrange.py api-one-model    # one case
"""

import subprocess
import sys

import yaml

MIN_CPU_M = 100
MIN_MEMORY_MI = 128


def cpu_millicores(value: str) -> int:
    text = str(value)
    return int(float(text[:-1])) if text.endswith("m") else int(float(text) * 1000)


def memory_mib(value: str) -> float:
    text = str(value)
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / (1024 * 1024), "M": 1000**2 / 1024**2, "G": 1000**3 / 1024**2}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text) / (1024 * 1024)


def containers(doc: dict):
    """Every container of every pod template in one rendered object."""
    pod = ((doc.get("spec") or {}).get("template") or {}).get("spec")
    if not pod:
        return
    for key in ("initContainers", "containers"):
        for container in pod.get(key) or []:
            yield container


def problems(kind: str, name: str, container: dict) -> list[str]:
    where = f"{kind}/{name} {container['name']}"
    resources = container.get("resources") or {}
    requests, limits = resources.get("requests") or {}, resources.get("limits") or {}

    if not requests and not limits:
        return [f"{where} declares no resources — the LimitRange fills in a 2:1 "
                f"limit/request pair and then refuses it"]

    found = []
    for resource, convert, floor, unit in (
        ("cpu", cpu_millicores, MIN_CPU_M, "m"),
        ("memory", memory_mib, MIN_MEMORY_MI, "Mi"),
    ):
        request, limit = requests.get(resource), limits.get(resource)
        if request is None or limit is None:
            found.append(f"{where} leaves {resource} "
                         f"{'request' if request is None else 'limit'} unset")
            continue
        if convert(request) != convert(limit):
            found.append(f"{where} {resource} request {request} != limit {limit}, "
                         f"and the ratio allowed is 1")
            continue
        if convert(request) < floor:
            found.append(f"{where} asks for {resource} {request}, under the "
                         f"{floor}{unit} floor")
    return found


def cases(only: str | None) -> list[tuple[str, str]]:
    found = []
    with open("charts/tests/cases.tsv", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            name, chart, repo, *_ = (line.rstrip("\n").split("\t") + ["", "", ""])[:4]
            # A chart pulled from a vendor repository carries whatever defaults its
            # author chose; what makes it admissible is the listing's own values,
            # which this render does not see.
            if repo or (only and name != only):
                continue
            found.append((name, chart))
    return found


def main(only: str | None) -> int:
    failures = 0
    for name, chart in cases(only):
        rendered = subprocess.run(
            ["helm", "template", "release", f"charts/{chart}",
             "--namespace", chart, "--values", f"charts/tests/cases/{name}.yaml"],
            capture_output=True, text=True, check=True,
        ).stdout

        found, checked = [], 0
        for doc in yaml.safe_load_all(rendered):
            if not doc:
                continue
            for container in containers(doc):
                checked += 1
                found += problems(doc["kind"], doc["metadata"]["name"], container)

        if found:
            failures += 1
            print(f"  FAIL  {name}")
            for problem in found:
                print(f"          {problem}")
        else:
            print(f"  ok    {name} ({checked} container(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
