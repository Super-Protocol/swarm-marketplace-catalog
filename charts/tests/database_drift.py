#!/usr/bin/env python3
"""
Does the database make the deployment's evidence digest a property of who deployed it?

A digest over a running namespace differs for every consumer unless every object in it
is identical for every consumer. Adding three PostgreSQL instances, four Services and a
bootstrap script to a listing is an opportunity to get that wrong — a generated
password outside a Secret, a namespace interpolated into a DSN, a hostname that reached
further than the Ingress — and the failure is invisible: it renders, it deploys, it
works, and `evidence.expectedDigest` can simply never be declared for the version.

So: render the parent chart twice, under two hostnames in two namespaces, and require
that every object the database chart contributed came out byte-identical. The api
chart's own objects are reported but not required to match — several of them carry the
hostname the operator chose, which is a pre-existing property of this listing and the
reason it declares no digest yet.

    charts/tests/database_drift.py confidential-router-api charts/tests/cases/api-one-model.yaml postgresql
"""

import json
import subprocess
import sys

import yaml

SHAPES = [("a", "space-a"), ("b", "space-b")]


def render(chart: str, values: str, tag: str, namespace: str) -> dict:
    manifests = subprocess.run(
        [
            "helm", "template", "cr", f"charts/{chart}",
            "--namespace", namespace,
            "--values", values,
            "--set", f"apiHostname=api.{tag}.example",
            "--set", f"consoleHostname=console.{tag}.example",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    documents = {}
    for document in yaml.safe_load_all(manifests):
        if not document:
            continue
        # The platform strips /metadata/namespace itself, so a difference there is not
        # one anybody has to declare.
        document["metadata"].pop("namespace", None)
        documents[(document["kind"], document["metadata"]["name"])] = document
    return documents


def main(chart: str, values: str, marker: str) -> int:
    (first, second) = (render(chart, values, tag, namespace) for tag, namespace in SHAPES)

    failures = 0
    identical = 0
    for key in sorted(set(first) | set(second)):
        if marker not in key[1]:
            continue
        here = json.dumps(first.get(key), sort_keys=True)
        there = json.dumps(second.get(key), sort_keys=True)
        if here == there:
            identical += 1
        else:
            print(f"  FAIL  {key[0]}/{key[1]} differs between two consumers")
            failures += 1

    if failures:
        return 1
    if identical == 0:
        print(f"  FAIL  no object matching {marker!r} was rendered — the check tested nothing")
        return 1
    print(f"  ok    {identical} database object(s), byte-identical for two consumers in two namespaces")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
