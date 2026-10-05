#!/usr/bin/env python3
"""
Two deployments of this version, and every field that differs is one the chart says may.

`cli/evidence-preview.js` asks this on the marketplace side. This asks it on the chart,
field by field rather than line by line: render the same chart as two consumers, under
two hostnames in two namespaces, and require every differing JSON Pointer to be covered
by the `swarm.io/exclude-evidence-fields` annotation on the object that holds it.

The line-based version of this check (confidential-s3's, in `run.sh`) passes as long as
no *unexpected string* appears in the diff. It cannot tell a field that is excluded from
a field that merely happens to contain an excluded hostname, and it cannot see a field
that differs by being absent on one side. Both matter here: `confidential-router`'s
hostname-derived values live in ConfigMaps whose whole `/data` is excluded, and one of
those keys is rendered only for a campaign (SUP-211).

Two more things are checked, because an exclusion that names the wrong field is worse
than none — it renders, deploys, and quietly admits one deployment:

  * every pointer in an annotation resolves to something in the document it is on;
  * the chart's annotations and the listing's `evidence.exclude` block say the same
    thing, in **both** directions. That block is what the marketplace reads to show the
    exclusions beside the digest, so a chart that excludes more than the listing admits
    is a digest displayed next to an incomplete answer — and a listing that claims an
    exclusion the chart no longer applies is a disclosure of something that is not
    happening, which is the worse of the two. The second direction cannot be seen by the
    drift comparison above: if the field stopped being rendered at all, nothing differs
    and nothing fails, while the listing goes on advertising that it was left out.

The reverse direction is asked only about the entries this chart owns — the ones naming
an object called `<chart>` or `<chart>-…`. A listing deploys several charts and vendors
others it cannot annotate, and each of those is a question for whoever renders them.

    charts/tests/evidence_drift.py confidential-router-api \
        charts/tests/cases/api-campaign.yaml confidential-router \
        apiHostname=api.{t}.example consoleHostname=console.{t}.example
"""

import subprocess
import sys

import yaml

ANNOTATION = "swarm.io/exclude-evidence-fields"

# The platform strips these itself, so a difference in one is not an exclusion anybody
# has to declare: the namespace a consumer deploys into, and the deployment id it
# stamps on every object.
PLATFORM_STRIPPED = ("/metadata/namespace", "/metadata/labels/swarm.cloud~1app-deployment-id")

SHAPES = [("a", "space-a"), ("b", "space-b")]


def escape(token: str) -> str:
    """RFC 6901: `~` is `~0` and `/` is `~1`, in that order."""
    return token.replace("~", "~0").replace("/", "~1")


def render(chart: str, values: str, tag: str, namespace: str, overrides: list[str]) -> dict:
    manifests = subprocess.run(
        ["helm", "template", "cr", f"charts/{chart}", "--namespace", namespace, "--values", values]
        + [arg for override in overrides for arg in ("--set", override.replace("{t}", tag))],
        capture_output=True, text=True, check=True,
    ).stdout

    documents = {}
    for document in yaml.safe_load_all(manifests):
        if document:
            documents[(document["kind"], document["metadata"]["name"])] = document
    return documents


def differing_pointers(left, right, pointer="") -> list[str]:
    """Deepest pointers at which two documents disagree, as JSON Pointers."""
    if isinstance(left, dict) and isinstance(right, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            child = f"{pointer}/{escape(str(key))}"
            if key not in left or key not in right:
                found.append(child)
            else:
                found.extend(differing_pointers(left[key], right[key], child))
        return found
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        found = []
        for index, (here, there) in enumerate(zip(left, right)):
            found.extend(differing_pointers(here, there, f"{pointer}/{index}"))
        return found
    return [] if left == right else [pointer or "/"]


def declared(document) -> list[str]:
    value = (document.get("metadata", {}).get("annotations") or {}).get(ANNOTATION)
    return [part.strip() for part in value.split(",")] if value else []


def covers(excluded: str, pointer: str) -> bool:
    return pointer == excluded or pointer.startswith(f"{excluded}/")


def resolves(document, pointer: str) -> bool:
    node = document
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(token)] if isinstance(node, list) else node[token]
        except (KeyError, IndexError, ValueError, TypeError):
            return False
    return True


def listing_exclusions(app: str) -> set[tuple[str, str, str]]:
    definition = yaml.safe_load(open(f"apps/{app}/app.yaml"))
    entries = (definition.get("evidence") or {}).get("exclude") or []
    return {
        (entry.get("match", {}).get("kind", ""), entry.get("match", {}).get("name", ""), field)
        for entry in entries
        for field in entry["fields"]
    }


def owns(chart: str, name: str) -> bool:
    """Whether an object of this name is one this chart renders, by naming convention."""
    return name == chart or name.startswith(f"{chart}-")


def main(chart: str, values: str, app: str, overrides: list[str]) -> int:
    first, second = (render(chart, values, tag, namespace, overrides) for tag, namespace in SHAPES)
    from_listing = listing_exclusions(app) if app != "-" else None

    failures = 0
    excluded_total = 0
    compared = 0

    for key in sorted(set(first) | set(second)):
        kind, name = key
        here, there = first.get(key), second.get(key)
        if here is None or there is None:
            print(f"  FAIL  {kind}/{name} is rendered for one consumer and not the other")
            failures += 1
            continue

        exclusions = declared(here)
        for pointer in exclusions:
            if not resolves(here, pointer):
                print(f"  FAIL  {kind}/{name} excludes {pointer}, which the rendered object has no field at")
                failures += 1
            if from_listing is not None and (kind, name, pointer) not in from_listing:
                print(f"  FAIL  {kind}/{name} excludes {pointer}, which apps/{app}/app.yaml does not declare")
                failures += 1

        for pointer in differing_pointers(here, there):
            if pointer in PLATFORM_STRIPPED:
                continue
            if any(covers(excluded, pointer) for excluded in exclusions):
                excluded_total += 1
            else:
                print(f"  FAIL  {kind}/{name}{pointer} differs between two consumers and is not excluded")
                failures += 1
        compared += 1

    # The other direction: an entry this chart owns has to be an exclusion the chart
    # actually applies, on an object the chart actually renders.
    claimed = 0
    for kind, name, pointer in sorted(from_listing or ()):
        if not owns(chart, name):
            continue
        claimed += 1
        rendered = first.get((kind, name))
        if rendered is None:
            print(f"  FAIL  apps/{app}/app.yaml declares {pointer} on {kind}/{name}, which this chart does not render")
            failures += 1
        elif pointer not in declared(rendered):
            print(f"  FAIL  apps/{app}/app.yaml declares {pointer} on {kind}/{name}, which the object does not exclude")
            failures += 1

    if failures:
        return 1
    print(f"  ok    {compared} object(s) compared, {excluded_total} differing field(s), all of them declared")
    if from_listing is not None:
        print(f"  ok    {claimed} exclusion(s) this chart owns in apps/{app}/app.yaml, each one applied by the chart")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]))
