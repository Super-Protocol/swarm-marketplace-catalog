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

CONSUMER = re.compile(r"\{\{\s*consumer\.[A-Za-z0-9_.]+\s*\}\}")

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
    elif isinstance(node, str) and CONSUMER.search(node):
        yield prefix


def main(app: str, requested: list[str]) -> int:
    definition = yaml.safe_load(open(f"apps/{app}/app.yaml"))
    failures = 0

    # `<chart>=<path>` picks the values a component is probed with; a bare path is
    # the one every component uses.
    default_values = next((arg for arg in requested if "=" not in arg), None)
    per_chart = dict(arg.split("=", 1) for arg in requested if "=" in arg)

    for component in definition["components"]:
        chart = component["source"]["chart"]
        base = component["deployment"]["values"].get("base", {})
        paths = sorted(set(consumer_paths(base)))

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
        markers = {path: f"consumer-probe-{index}@marker.invalid" for index, path in enumerate(paths)}
        sets = [f"--set-string={path}={marker}" for path, marker in markers.items()]
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
