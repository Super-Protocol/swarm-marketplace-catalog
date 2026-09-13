#!/usr/bin/env python3
"""
Every image a chart ships is pinned by a real digest in its own values.

The golden renders cannot answer this: the cases set their image digests
explicitly, so the goldens are stable across an image bump and say nothing about
what the chart itself would pull. A listing overrides the defaults too — which
leaves an empty or placeholder default invisible until somebody runs
`helm install` with no `--set`, or until a chart is published to an append-only
repository carrying a digest nobody can pull.

    charts/tests/digests.py confidential-s3
"""

import re
import sys

import yaml

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER = "sha256:" + "0" * 64

# Keyed by chart: the values paths that hold an image this chart deploys.
IMAGES = {
    "confidential-s3": [
        "gateway.image",
        "api.image",
        "console.image",
        "garage.image",
        # The vendored subchart names its own image by tag, which is the
        # vendor's contract; the listing rewrites it to a digest.
    ],
}


def main(chart: str) -> int:
    values = yaml.safe_load(open(f"charts/{chart}/values.yaml"))
    failures = 0

    for path in IMAGES[chart]:
        node = values
        for key in path.split("."):
            node = node[key]
        digest = node.get("digest") or ""

        if not digest:
            print(f"  FAIL  {chart}: {path}.digest is empty")
            failures += 1
        elif digest == PLACEHOLDER:
            print(f"  FAIL  {chart}: {path}.digest is the all-zero placeholder")
            failures += 1
        elif not DIGEST.match(digest):
            print(f"  FAIL  {chart}: {path}.digest is not a sha256 reference: {digest}")
            failures += 1
        else:
            print(f"  ok    {path} → {node['registry']}/{node['repository']}@{digest[:19]}…")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
