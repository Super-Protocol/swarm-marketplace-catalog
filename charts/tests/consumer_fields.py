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

    charts/tests/consumer_fields.py confidential-s3 charts/tests/cases/s3-default.yaml
"""

import json
import re
import subprocess
import sys

import yaml

CONSUMER = re.compile(r"\{\{\s*consumer\.[A-Za-z0-9_.]+\s*\}\}")

# Values a listing is allowed to pass as a literal, with the reason. Empty is the
# right length for this list.
EXPECTED_LITERALS: dict[str, set[str]] = {}


def consumer_paths(node, prefix=""):
    """Every values path a definition fills from a `consumer.*` expression."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from consumer_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, str) and CONSUMER.search(node):
        yield prefix


def main(app: str, values: str) -> int:
    definition = yaml.safe_load(open(f"apps/{app}/app.yaml"))
    failures = 0

    for component in definition["components"]:
        chart = component["source"]["chart"]
        base = component["deployment"]["values"].get("base", {})
        paths = sorted(set(consumer_paths(base)))

        if not paths:
            print(f"  ok    {app}/{component['name']}: passes no consumer value to {chart}")
            continue

        # Render the chart with a consumer-shaped marker in each of those paths and
        # look for the marker in the objects a cloud attests.
        # Shaped like the values it stands in for — the chart refuses an admin
        # address without an `@`, and a probe that cannot render proves nothing.
        marker = "consumer-probe@marker.invalid"
        sets = [f"--set-string={path}={marker}" for path in paths]
        rendered = subprocess.run(
            ["helm", "template", "probe", f"charts/{chart}", "--namespace", app,
             "--values", values, *sets],
            capture_output=True, text=True, check=True,
        ).stdout

        attested = {"Ingress", "Service", "Deployment", "StatefulSet", "DaemonSet", "ConfigMap"}
        leaked = []
        for doc in yaml.safe_load_all(rendered):
            if not doc or doc.get("kind") not in attested:
                continue
            if marker in json.dumps(doc):
                leaked.append(f"{doc['kind']}/{doc['metadata']['name']}")

        allowed = EXPECTED_LITERALS.get(app, set())
        for where in sorted(set(leaked)):
            if where in allowed:
                print(f"  ok    {where} carries a consumer value, declared in EXPECTED_LITERALS")
            else:
                print(f"  FAIL  {where} carries a consumer value as a literal — it will vary the evidence digest")
                failures += 1

        if not leaked:
            print(f"  ok    {', '.join(paths)} reach {chart} without landing in an attested object")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
