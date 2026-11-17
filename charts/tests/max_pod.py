#!/usr/bin/env python3
"""
A listing's `resources.maxPod` is the heaviest pod its charts actually render.

`resources.min` is the cluster space's quota and `resources.maxPod` is the
heaviest single pod inside it. The cloud uses `maxPod` twice: to require that
one node can hold that pod, and as the LimitRange `max` of the space. Declared
too small, both bite — the pod is refused at admission by a LimitRange sized
from it, or stays Pending because placement chose a cluster for a pod smaller
than the real one — and neither presents as a resource declaration that was
wrong. It presents as "the app is broken" (SUP-824, and SUP-238 for the
LimitRange half).

Omitting `maxPod` is safe: the cloud falls back to `min`. Declaring it is what
needs checking, and only the render can check it, so:

    pod demand = its containers' limits SUMMED,
                 or its heaviest init container, whichever is larger

per pod, across every component the listing deploys. (A native sidecar — an init
container with `restartPolicy: Always` — is summed with the regular ones, since
it runs beside them.) Note the difference from
`limitrange.py`, which checks the same manifests per CONTAINER: a pod of four
200m containers is admissible there and is a 800m pod here.

The marketplace's own egress sidecar is not counted. It is a Deployment of its
own (`egress-sidecar.ts`), so it inflates no component's pod — it is `min`'s
business, not this one's.

    charts/tests/max_pod.py conversational-analyst \\
        platform:conversational-analyst-platform:analyst-platform \\
        embeddings:conversational-analyst-embeddings:analyst-embeddings
"""

from __future__ import annotations

import subprocess
import sys

import yaml
from limitrange import containers, cpu_millicores, memory_mib, pod_spec


def rendered(chart: str, case: str) -> str:
    return subprocess.run(
        # `--skip-tests`, the flag the marketplace renders with: a `helm test` hook is a Pod
        # the cloud never applies, so its resources are not a pod this listing schedules.
        ["helm", "template", "release", f"charts/{chart}", "--skip-tests",
         "--namespace", chart, "--values", f"charts/tests/cases/{case}.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout


def pod_demand(doc: dict) -> tuple[int, float] | None:
    """One rendered object's heaviest pod, as (millicores, MiB), or None for a pod-less object.

    A pod's demand on a node is its regular containers' limits summed, or its heaviest init
    container, whichever is larger — an init container runs alone, before any of them start.
    """
    pod = pod_spec(doc)
    if not pod:
        return None

    def limits(container: dict) -> tuple[int, float]:
        declared = (container.get("resources") or {}).get("limits") or {}
        cpu = declared.get("cpu")
        memory = declared.get("memory")
        # A container with no limit is `limitrange.py`'s failure to report, not this one's: it
        # is refused at admission, so there is no pod here to weigh. Counted as zero rather
        # than guessed at, so the two checks do not disagree about the same manifest.
        return (cpu_millicores(cpu) if cpu else 0, memory_mib(memory) if memory else 0)

    # A native sidecar — an init container with `restartPolicy: Always` — runs beside the regular
    # containers for the life of the pod, so it is summed with them rather than weighed against
    # them. No chart here renders one today; getting it wrong would under-count a pod, which is
    # the direction that costs an outage.
    everything = list(pod.get("containers") or [])
    init = []
    for container in pod.get("initContainers") or []:
        (everything if container.get("restartPolicy") == "Always" else init).append(container)

    concurrent = [limits(c) for c in everything]
    sequential = [limits(c) for c in init]
    return (
        max(sum(cpu for cpu, _ in concurrent), max((cpu for cpu, _ in sequential), default=0)),
        max(sum(mem for _, mem in concurrent), max((mem for _, mem in sequential), default=0)),
    )


def heaviest(listing_components: list[tuple[str, str, str]]) -> list[tuple[int, float, str]]:
    """Every rendered pod of every component, heaviest first, each with where it came from."""
    pods: list[tuple[int, float, str]] = []
    for component, chart, case in listing_components:
        for doc in yaml.safe_load_all(rendered(chart, case)):
            if not doc:
                continue
            demand = pod_demand(doc)
            if demand is None:
                continue
            cpu, memory = demand
            pods.append((cpu, memory, f"{component} {doc['kind']}/{doc['metadata']['name']}"))
    return sorted(pods, reverse=True)


def declared(listing: str) -> dict[str, str] | None:
    with open(f"apps/{listing}/app.yaml", encoding="utf-8") as handle:
        resources = (yaml.safe_load(handle) or {}).get("resources") or {}
    return resources.get("maxPod")


def main(listing: str, specs: list[str]) -> int:
    max_pod = declared(listing)
    if not max_pod:
        print(f"  ok    apps/{listing}/app.yaml declares no maxPod; the cloud falls back to min")
        return 0

    allowed_cpu = cpu_millicores(max_pod["cpu"])
    allowed_memory = memory_mib(max_pod["memory"])

    components: list[tuple[str, str, str]] = []
    for spec in specs:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            raise SystemExit(f"  FAIL  {spec!r} is not <component>:<chart>:<case>")
        components.append((parts[0], parts[1], parts[2]))

    pods = heaviest(components)
    if not pods:
        # Exiting 0 having weighed nothing would read as a pass on a listing whose charts render
        # no pods at all, which is not a thing that happens for a real reason.
        print(f"  FAIL  no component of apps/{listing}/app.yaml rendered a pod")
        return 1

    print(f"  ok    {len(pods)} pod(s) weighed across {len(specs)} component(s)")

    failures = 0
    for resource, index, allowed, fmt in (
        ("cpu", 0, allowed_cpu, lambda v: f"{v}m"),
        ("memory", 1, allowed_memory, lambda v: f"{v:g}Mi"),
    ):
        worst = max(pods, key=lambda pod: pod[index])
        declaration = f'maxPod.{resource} "{max_pod[resource]}"'
        if worst[index] > allowed:
            print(f"  FAIL  {worst[2]} is a {fmt(worst[index])} pod, over the {declaration} "
                  f"apps/{listing}/app.yaml declares")
            failures += 1
        else:
            print(f"  ok    heaviest {resource} pod is {worst[2]} at {fmt(worst[index])}, "
                  f"within {declaration}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
