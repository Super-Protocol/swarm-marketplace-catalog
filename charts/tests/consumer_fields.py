#!/usr/bin/env python3
"""
No attested manifest may carry a literal that names the consumer.

This is the check the repository did not have, and a deployment found instead: in
confidential-s3 0.1.0 the control plane's environment carried
`BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_WORKSPACE_NAME` as plain values, filled
from `consumer.user.email` and `consumer.organization.name`. Both render, deploy
and work — and make the evidence digest a property of *who deployed it*, so no
two consumers can ever agree on one and `expectedDigest` can never be declared.

`charts/tests/run.sh` already renders each chart as two consumers and diffs. It
could not see this: it renders the chart with fixed values, while `consumer.*` is
resolved by the *marketplace* when it turns a listing into values. The gap is the
seam between the listing and the chart, so the check has to look at the listing.

What it does: reads every `consumer.*` expression out of a definition's component
values, follows it to the chart value it lands on, and requires that value to
reach the container through a Secret rather than as a literal in an attested
object. A definition that must pass a consumer value as a literal has to say so
in EXPECTED_LITERALS, with a reason.

The values a component is probed with matter: a chart renders an env var only on
the path that uses it, so a case that leaves that path off hides the literal
rather than clearing it. `confidential-router` is exactly that — the deployer's
address reaches the pod only alongside a first-sign-in token, so the probe has to
be the bootstrap case, and the listing shipped the literal for four versions with
this check passing on a different listing (SUP-241). Hence one values file per
chart rather than one for the definition.

    charts/tests/consumer_fields.py confidential-s3 charts/tests/cases/s3-default.yaml
    charts/tests/consumer_fields.py confidential-router \
        confidential-router-api=charts/tests/cases/api-bootstrap-token.yaml
"""

import json
import os
import re
import subprocess
import sys

import yaml

CONSUMER = re.compile(r"\{\{\s*consumer\.([A-Za-z0-9_.]+)\s*\}\}")


def marker_for(index: int, field: str) -> str:
    """A probe value shaped like the thing it stands in for.

    One shape does not fit all of them, and a probe that cannot render proves nothing — which
    is a silent pass, not a visible failure. An address needs an `@` or a chart that validates
    admin emails refuses it; a slug is a DNS label, and a chart that validates one refuses an
    address. So the shape comes from the consumer field the expression names.
    """
    if field.endswith("email"):
        return f"consumer-probe-{index}@marker.invalid"
    # Valid as a DNS label, a slug, a plain name and a Keycloak username alike.
    return f"consumer-probe-{index}-marker"

# Values a listing is allowed to pass as a literal, with the reason. Empty is the
# right length for this list.
EXPECTED_LITERALS: dict[str, set[str]] = {}

# The kinds a cloud collects and signs. A value in any of them is published.
ATTESTED = {"Ingress", "Service", "Deployment", "StatefulSet", "DaemonSet", "ConfigMap"}


def consumer_paths(node, prefix=""):
    """Every values path a definition fills from a `consumer.*` expression.

    Lists are walked too, and addressed the way `helm --set` addresses them:
    `auth.adminEmails[0]`. Without that an address passed as a one-entry list is
    invisible to this check — which is how `confidential-router` came to have one
    copy of the deployer's address sealed and the other published.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from consumer_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from consumer_paths(value, f"{prefix}[{index}]")
    elif isinstance(node, str):
        found = CONSUMER.search(node)
        if found:
            yield prefix, found.group(1)


def helm_path(path: str) -> str:
    """`a.0.b` as helm addresses it: `a[0].b`."""
    out = ""
    for segment in path.split("."):
        if segment.isdigit():
            out += f"[{segment}]"
        else:
            out += f".{segment}" if out else segment
    return out


def patch_consumer_paths(patches):
    """Every values path a definition fills from `consumer.*` through a PATCH.

    Not an afterthought — it is where most of them are. A definition that has to put a
    consumer value somewhere usually declares the key in `base` as an empty string and fills
    it in a patch, because that is the shape a reader can see the default of. Reading only
    `base` therefore sees `username: ""`, finds no expression, and reports that the component
    passes no consumer value at all.

    `conversational-analyst` is exactly that: it passed `consumer.user.email` and
    `consumer.user.name` to the realm bootstrap in patches for every version up to 4.0.0,
    with the values landing in a ConfigMap, and this check called it clean each time.

    A patch's `value` may be a scalar or a structure. A scalar at `path` is that path; a
    structure is walked, and its leaves are addressed under it the same way `base`'s are.
    """
    for patch in patches or []:
        if patch.get("op") == "remove":
            continue
        root = patch.get("path", "")
        value = patch.get("value")
        if isinstance(value, str):
            found = CONSUMER.search(value)
            if found:
                yield root, found.group(1)
        else:
            for suffix, field in consumer_paths(value):
                yield (f"{root}.{suffix}" if suffix else root), field


def main(app: str, requested: list[str]) -> int:
    definition = yaml.safe_load(open(f"apps/{app}/app.yaml"))
    failures = 0

    # `<chart>=<path>` picks the values a component is probed with; a bare path is
    # the one every component uses.
    default_values = next((arg for arg in requested if "=" not in arg), None)
    per_chart = dict(arg.split("=", 1) for arg in requested if "=" in arg)

    for component in definition["components"]:
        chart = component["source"]["chart"]
        values_block = component["deployment"]["values"]
        base = values_block.get("base", {})
        fields = dict(consumer_paths(base))
        fields.update(dict(patch_consumer_paths(values_block.get("patches"))))
        paths = sorted(fields)

        if not paths:
            print(f"  ok    {app}/{component['name']}: passes no consumer value to {chart}")
            continue

        values = per_chart.get(chart, default_values)
        if values is None:
            # Not a pass: a component whose probe values nobody named is a
            # component this check did not look at.
            print(f"  FAIL  {app}/{component['name']} fills {', '.join(paths)} and no case values were given for {chart}")
            failures += 1
            continue
        if not os.path.isdir(f"charts/{chart}"):
            print(f"  FAIL  {app}/{component['name']} fills {', '.join(paths)} in {chart}, which is not a chart this repository renders")
            failures += 1
            continue

        # Render the chart with a consumer-shaped marker in each of those paths and
        # look for each marker in the objects a cloud attests. One marker per path
        # rather than one for the component: two paths can carry the same address
        # — `confidential-router` passes it to both `bootstrapEmail` and
        # `adminEmails` — and a shared marker cannot say which of them published
        # it, nor that one of them never rendered at all.
        # Shaped like the values they stand in for: the chart refuses an admin
        # address without an `@`, and a probe that cannot render proves nothing.
        markers = {path: marker_for(index, fields[path]) for index, path in enumerate(paths)}
        # A definition addresses a list element with a numeric path segment
        # (`organizations.0.alias`, which is the only form its schema's path pattern allows);
        # `helm --set` wants brackets. Translating here rather than storing two spellings
        # keeps the names this check PRINTS the ones a reader will find in the definition.
        sets = [f"--set-string={helm_path(path)}={marker}" for path, marker in markers.items()]
        rendered = subprocess.run(
            ["helm", "template", "probe", f"charts/{chart}", "--namespace", app,
             "--values", values, *sets],
            capture_output=True, text=True, check=True,
        ).stdout

        documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
        allowed = EXPECTED_LITERALS.get(app, set())

        for path, marker in markers.items():
            # A path that reaches nothing is a path this check did not look at,
            # and that is the half SUP-241 was missing: the deployer's address is
            # rendered only alongside a first-sign-in token, so on any other case
            # values it is neither published nor probed — and an unrendered
            # literal looks exactly like a sealed one.
            if marker not in rendered:
                print(f"  FAIL  {path} does not reach {chart}'s render on {values} — nothing was probed")
                failures += 1
                continue

            leaked = sorted({
                f"{doc['kind']}/{doc['metadata']['name']}"
                for doc in documents
                if doc.get("kind") in ATTESTED and marker in json.dumps(doc)
            })
            for where in leaked:
                if where in allowed:
                    print(f"  ok    {where} carries {path}, declared in EXPECTED_LITERALS")
                else:
                    print(f"  FAIL  {where} carries {path} as a literal — it will vary the evidence digest")
                    failures += 1

            if not leaked:
                print(f"  ok    {path} reaches {chart} without landing in an attested object")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
