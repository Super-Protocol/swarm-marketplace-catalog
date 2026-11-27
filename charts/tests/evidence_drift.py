#!/usr/bin/env python3
"""
Two deployments of this version, and every field that differs is one the chart says may.

`cli/evidence-preview.js` asks this on the marketplace side. This asks it on the chart,
field by field rather than line by line: render the same chart as two consumers, under
two hostnames in two namespaces, and require every differing JSON Pointer to be covered
by the `swarm.io/exclude-evidence-fields` annotation on the object that holds it.

"Two consumers" means everything a consumer brings, not only the hostname they typed.
The caller decides what else varies between the two shapes; for `confidential-router`
that includes the deployer's own address, which the marketplace fills from the account
clicking deploy. Varying only the hostname is what let the address be published for
four versions with this check green (SUP-241).

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

import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

ANNOTATION = "swarm.io/exclude-evidence-fields"

# The platform strips these itself, so a difference in one is not an exclusion anybody
# has to declare: the namespace a consumer deploys into, and the deployment id it
# stamps on every object.
PLATFORM_STRIPPED = ("/metadata/namespace", "/metadata/labels/swarm.cloud~1app-deployment-id")

# And a Secret's values never reach the snapshot at all: the canonical rules drop
# `/data`, `/stringData` and `/immutable` from every Secret, so only its name and
# key-less shell are attested. That is what makes a Secret the carrier for a value
# which is neither a credential nor publishable — the deployer's own address, which
# this chart handed the API as a plain env value until SUP-241. Modelled here
# because without it the comparison below cannot be asked about anything a consumer
# brings: the Secret differs, and the difference is one no listing may declare.
SECRET_STRIPPED = ("/data", "/stringData", "/immutable")

SHAPES = [("a", "space-a"), ("b", "space-b")]

# The kinds a cloud's collector actually scans — `EVIDENCE_COLLECTION_KINDS` in swarm-cloud's
# deployment-evidence-canonical, read off `origin/develop` (2026-11-17).
#
# Comparing anything else reports differences that can never reach a digest, and the comparison
# then cannot converge however much a listing excludes. `Job` is the one that bites: its name is
# routinely derived — a hash of the consumer, or of the dataset bindings — so a chart that renders
# one fails this check forever, with "rendered for one consumer and not the other" about an object
# no cloud attests. `PersistentVolumeClaim` is out for the mirror reason: the instances a
# `volumeClaimTemplates` makes exist online and not in a rendered manifest (SUP-416).
#
# `Pod` is collected and then dropped by the canonical rules, so there is nothing to compare.
COLLECTED_KINDS = frozenset({
    "Ingress", "Service", "Deployment", "StatefulSet", "DaemonSet", "CronJob",
    "HorizontalPodAutoscaler", "PodDisruptionBudget", "ConfigMap", "Secret",
    "ServiceAccount", "Role", "RoleBinding",
})


def escape(token: str) -> str:
    """RFC 6901: `~` is `~0` and `/` is `~1`, in that order."""
    return token.replace("~", "~0").replace("/", "~1")


def render(chart: str, values: str, tag: str, namespace: str, overrides: list[str]) -> dict:
    # The release name is the chart name, because that is the release the marketplace uses: it
    # renders each component as `<listing name>-<component name>` (`releaseNameFor`, with
    # `releasePrefix = shortName(listing.name)`), and every chart here is named for the component
    # it carries. It matters for any chart that does not pin a `fullnameOverride`, because then
    # its object names come from the release — and an `evidence.exclude` entry is matched against
    # the rendered name by an exact string compare. Rendering under some other release was how
    # 4.0.1 of the analyst shipped an exclusion naming an object no deployment produces: the
    # entry said `analyst-config`, the deployment rendered `conversational-analyst-agent-config`,
    # and this check could see neither (SUP-823). It is also the convention `owns()` below
    # already assumes.
    manifests = subprocess.run(
        ["helm", "template", chart, f"charts/{chart}", "--namespace", namespace, "--values", values]
        + [arg for override in overrides for arg in ("--set", override.replace("{t}", tag))],
        capture_output=True, text=True, check=True,
    ).stdout

    documents = {}
    for document in yaml.safe_load_all(manifests):
        if document and document.get("kind") in COLLECTED_KINDS:
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
    """
    Whether this chart renders an object of this name from a template of its own.

    Two conditions, and the second is not redundant. The name has to look like this chart's,
    which is the convention every chart here follows. But a chart with no `templates/` directory
    renders nothing itself — it is a wrapper whose objects all come from a dependency — and
    those objects take their names from the Helm release, so they look exactly like the
    wrapper's own. `conversational-analyst-agent` is that chart: Chart.yaml, values.yaml, a
    Chart.lock, and every object from the vendored aivo chart. Asking such a chart to carry an
    annotation it has no template to put it on is asking for the impossible, and the authoring
    guide's answer for exactly this case is to declare the exclusion in the definition instead.
    """
    looks_like_ours = name == chart or name.startswith(f"{chart}-")
    return looks_like_ours and (ROOT / "charts" / chart / "templates").is_dir()


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

        # What excludes a field is the annotation on the object as the CLOUD receives it, and the
        # marketplace builds that by merging the chart's own annotation with every entry of the
        # definition's `evidence.exclude` that matches the object (`stampEvidenceExclusions` in
        # deployment-bundle.service.ts). So a definition entry genuinely excludes the field, and a
        # check that only accepted the chart's annotation would refuse the one case the authoring
        # guide names for the definition: an object rendered by a third-party chart, which cannot
        # be annotated at all. Every `conversational-analyst` exclusion is of that kind — bitnami's
        # Keycloak ConfigMap, the vendored Redis StatefulSet, the aivo config Deployment.
        #
        # The strictness this drops is still enforced, in the other direction, below: an exclusion
        # on an object THIS chart owns must be in the chart's annotation, so "annotate your own
        # chart" survives.
        exclusions = declared(here) + [
            pointer for kind_, name_, pointer in (from_listing or ())
            if (not kind_ or kind_ == kind) and (not name_ or name_ == name)
        ]
        for pointer in declared(here):
            if not resolves(here, pointer):
                print(f"  FAIL  {kind}/{name} excludes {pointer}, which the rendered object has no field at")
                failures += 1
            if from_listing is not None and (kind, name, pointer) not in from_listing:
                print(f"  FAIL  {kind}/{name} excludes {pointer}, which apps/{app}/app.yaml does not declare")
                failures += 1

        # And the same resolution question for the entries the LISTING declares, which until now
        # nothing asked. A chart's annotation is checked above; a definition entry naming an
        # object a vendored chart renders was checked nowhere, and those are the ones most likely
        # to rot — a bitnami upgrade renaming a checksum annotation leaves the entry declared,
        # unapplied and silent, and the field it meant to exclude starts moving the digest again.
        # That is how `analyst-config` went unnoticed through 4.0.1, in the one form this check
        # could not see.
        #
        # Only entries that name a SPECIFIC object, and only where this render has it. A
        # wildcard entry — no `match`, or no `name` — is a claim about a field that may or may
        # not exist on each object, not about one object: `/metadata/namespace` is declared that
        # way and 4 of the identity chart's 12 rendered objects do not carry it, because whether
        # a template writes it is the template's business. Checking those would be four
        # failures about nothing.
        for kind_, name_, pointer in sorted(from_listing or ()):
            if not kind_ or not name_ or (kind_, name_) != (kind, name):
                continue
            if not resolves(here, pointer):
                print(f"  FAIL  apps/{app}/app.yaml excludes {kind}/{name}{pointer}, "
                      f"which the rendered object has no field at")
                failures += 1

        stripped = PLATFORM_STRIPPED + (SECRET_STRIPPED if kind == "Secret" else ())
        for pointer in differing_pointers(here, there):
            if any(covers(prefix, pointer) for prefix in stripped):
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
