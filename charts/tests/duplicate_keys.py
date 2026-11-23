#!/usr/bin/env python3
"""
No rendered pod spec defines the same key twice.

Kubernetes writes a manifest with SERVER-SIDE APPLY, and several of the lists in
a pod spec are not lists to it: they are maps, keyed by a field, declared as
`listType: map` in the built-in schema. Applying a document that carries the same
key twice is rejected outright, before anything is merged:

    .spec.template.spec.containers[name="retail-ai"].env: duplicate entries for key

That failure does NOT present where it was made. A duplicate with two identical
values renders fine, lints fine, passes a golden diff, starts, serves traffic and
digests to a stable snapshot — so a listing can be authored, measured, published
and demonstrated with one in it. What it breaks is the write: the cloud's
`reconfigure` applies the rendered documents over a live deployment, and a
published cluster space has no other way in. `conversational-analyst-agent` 0.5.1
and 0.6.0 both defined RAI_PROCESS_ROLE twice on the `retail-ai` Deployment — the
vendored subchart emitted it from `worker.enabled` and `back.extraEnv` added an
identical copy — and the consequence was a published stand that could not be
reconfigured at all, found only when somebody tried (SUP-896, fixed in 0.6.1 on
SUP-899).

So this is checked against the render of every local case, not against the chart
that caused it: the half a listing supplies and the half a vendored subchart
emits are only ever one document together, which is the shape the defect needs.

The keys checked are the `listType: map` lists of a pod spec, each with the key
the API server actually uses:

    pod.spec.volumes                            name
    pod.spec.{initContainers,containers}        name
    container.env                               name
    container.ports                             containerPort + protocol, and name
    container.volumeMounts                      mountPath

`envFrom` is deliberately absent: it is atomic, and two entries naming one
ConfigMap are legal (the later wins, as the kubelet documents).

    charts/tests/duplicate_keys.py                 # every local case in cases.tsv
    charts/tests/duplicate_keys.py analyst-agent   # one case
"""

import collections
import subprocess
import sys

import yaml


def pod_spec(doc: dict) -> dict | None:
    """The pod spec one rendered object carries, wherever it keeps it.

    The same three shapes `limitrange.py` reads, and for the same reason: a pod
    spec that escapes this function is never checked.
    """
    spec = doc.get("spec") or {}
    if doc.get("kind") == "Pod":
        return spec or None
    if doc.get("kind") == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
    return ((spec.get("template") or {}).get("spec")) or None


def duplicates(entries, key) -> list[str]:
    """Every key that two or more entries of one list claim."""
    seen = collections.Counter()
    for entry in entries:
        if isinstance(entry, dict):
            seen[key(entry)] += 1
    return [str(name) for name, count in seen.items() if count > 1 and name is not None]


def problems(doc: dict) -> list[str]:
    pod = pod_spec(doc)
    if not pod:
        return []
    where = f"{doc['kind']}/{doc['metadata']['name']}"
    found = []

    containers = (pod.get("initContainers") or []) + (pod.get("containers") or [])
    for label, entries, key in (
        ("volumes", pod.get("volumes") or [], lambda v: v.get("name")),
        ("containers", containers, lambda c: c.get("name")),
    ):
        for name in duplicates(entries, key):
            found.append(f"{where} defines {label} {name!r} twice")

    for container in containers:
        place = f"{where} {container.get('name')}"
        for label, entries, key, render in (
            ("env", container.get("env") or [], lambda e: e.get("name"), repr),
            ("volumeMounts", container.get("volumeMounts") or [],
             lambda m: m.get("mountPath"), repr),
            # A port is keyed by the pair, and separately by its name when it has
            # one — two entries can collide on either and only one of the two is
            # visible in a diff of the numbers.
            ("ports", container.get("ports") or [],
             lambda p: (p.get("containerPort"), p.get("protocol", "TCP")), str),
            ("named ports", [p for p in (container.get("ports") or []) if p.get("name")],
             lambda p: p.get("name"), repr),
        ):
            for name in duplicates(entries, key):
                found.append(f"{place} defines {label} {render(name)} twice")
    return found


def cases(only: str | None) -> list[tuple[str, str]]:
    local, vendor = [], []
    with open("charts/tests/cases.tsv", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            name, chart, repo, *_ = (line.rstrip("\n").split("\t") + ["", "", ""])[:4]
            # A chart pulled from a vendor repository renders under whatever defaults
            # its author chose rather than under the listing's values, so a duplicate
            # the listing itself creates would not be in that render. Same boundary as
            # `limitrange.py` draws, and for the same reason.
            (vendor if repo else local).append((name, chart))

    if only is None:
        return local

    selected = [case for case in local if case[0] == only]
    if selected:
        return selected
    # Exiting 0 having checked nothing would read as a pass.
    if any(name == only for name, _ in vendor):
        raise SystemExit(f"  FAIL  {only} renders a vendor chart, which this check "
                         f"does not cover — see the comment in cases()")
    raise SystemExit(f"  FAIL  no case named {only} in charts/tests/cases.tsv")


def main(only: str | None) -> int:
    failures = 0
    for name, chart in cases(only):
        rendered = subprocess.run(
            # `--skip-tests`, because that is what the marketplace renders with and
            # therefore what it applies; a `helm test` hook is never written to the
            # cluster. Same flag and same reason as `limitrange.py`.
            ["helm", "template", "release", f"charts/{chart}", "--skip-tests",
             "--namespace", chart, "--values", f"charts/tests/cases/{name}.yaml"],
            capture_output=True, text=True, check=True,
        ).stdout

        found, checked = [], 0
        for doc in yaml.safe_load_all(rendered):
            if not doc or not pod_spec(doc):
                continue
            checked += 1
            found += problems(doc)

        if found:
            failures += 1
            print(f"  FAIL  {name}")
            for problem in found:
                print(f"          {problem}")
        else:
            print(f"  ok    {name} ({checked} pod spec(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
