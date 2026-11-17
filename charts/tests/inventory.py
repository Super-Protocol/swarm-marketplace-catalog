#!/usr/bin/env python3
"""
What a chart rendered, as a list of (kind, name), checked against what it is
supposed to render.

A golden diff cannot answer this. A template that loses a `---` produces two
objects glued into one document; the golden is regenerated from the same broken
render, the diff is empty, and `helm lint` parses the merged document as the
second object and finds nothing wrong. The cluster then applies one object and
silently drops the other — which is how a Service disappears and everything
downstream reads as "the app is broken".

So this parses the render as YAML documents, the way a cluster does, and names
every object it expects to find.

    charts/tests/inventory.py confidential-s3 charts/tests/cases/s3-default.yaml

A chart whose object list depends on its values is keyed once per shape, and the
key is given as a third argument:

    charts/tests/inventory.py confidential-router-ollama \\
        charts/tests/cases/ollama-no-models.yaml confidential-router-ollama:none
"""

import subprocess
import sys

import yaml

# Keyed by chart. Subchart objects are included: they are applied too, and a
# dependency that stops rendering its Service is the same failure.
EXPECTED = {
    "patroni-postgresql": {
        ("ConfigMap", "patroni-postgresql-etcd"),
        ("ConfigMap", "patroni-postgresql-router"),
        ("ConfigMap", "patroni-postgresql-scripts"),
        ("Deployment", "patroni-postgresql-router"),
        ("PodDisruptionBudget", "patroni-postgresql"),
        ("PodDisruptionBudget", "patroni-postgresql-etcd"),
        ("PodDisruptionBudget", "patroni-postgresql-router"),
        ("Secret", "patroni-postgresql"),
        ("Service", "patroni-postgresql"),
        ("Service", "patroni-postgresql-etcd"),
        ("Service", "patroni-postgresql-pods"),
        ("Service", "patroni-postgresql-repl"),
        ("StatefulSet", "patroni-postgresql"),
        ("StatefulSet", "patroni-postgresql-etcd"),
    },
    # The composed render, database included: this is the one place the whole
    # deployment's object list is named, and the database is where an object that
    # quietly fails to apply is a cluster that elects nobody.
    "confidential-router-api": {
        ("ConfigMap", "confidential-router-api"),
        # The hostname-derived half of the router's configuration, kept out of the
        # evidence snapshot so the digest is a property of the version rather than
        # of the hostname the operator chose (SUP-211).
        ("ConfigMap", "confidential-router-api-public"),
        ("ConfigMap", "confidential-router-postgresql-etcd"),
        ("ConfigMap", "confidential-router-postgresql-router"),
        ("ConfigMap", "confidential-router-postgresql-scripts"),
        ("Deployment", "confidential-router-api"),
        ("Deployment", "confidential-router-postgresql-router"),
        ("Ingress", "confidential-router-api"),
        ("PodDisruptionBudget", "confidential-router-postgresql"),
        ("PodDisruptionBudget", "confidential-router-postgresql-etcd"),
        ("PodDisruptionBudget", "confidential-router-postgresql-router"),
        ("Secret", "confidential-router-api"),
        ("Service", "confidential-router-api"),
        ("Service", "confidential-router-postgresql"),
        ("Service", "confidential-router-postgresql-etcd"),
        ("Service", "confidential-router-postgresql-pods"),
        ("Service", "confidential-router-postgresql-repl"),
        ("StatefulSet", "confidential-router-postgresql"),
        ("StatefulSet", "confidential-router-postgresql-etcd"),
    },
    # The local inference stack, with a selection and without one. Without one it
    # is a single inert ConfigMap per chart: no workload, no Service, no volume,
    # no Secret — the platform needs a component to render something, and that
    # is all it gets (SUP-245).
    "confidential-router-ollama": {
        ("Deployment", "confidential-router-ollama"),
        ("PersistentVolumeClaim", "confidential-router-ollama"),
        ("Service", "confidential-router-ollama"),
    },
    "confidential-router-ollama:none": {
        ("ConfigMap", "confidential-router-ollama"),
    },
    "confidential-router-litellm": {
        ("ConfigMap", "confidential-router-litellm"),
        ("Deployment", "confidential-router-litellm"),
        ("Secret", "confidential-router-litellm"),
        ("Service", "confidential-router-litellm"),
    },
    "confidential-router-litellm:none": {
        ("ConfigMap", "confidential-router-litellm"),
    },
    "confidential-s3": {
        ("ConfigMap", "confidential-s3-bootstrap"),
        ("ConfigMap", "confidential-s3-garage"),
        ("ConfigMap", "confidential-s3-gateway"),
        ("Deployment", "confidential-s3-api"),
        ("Deployment", "confidential-s3-console"),
        ("Deployment", "confidential-s3-gateway"),
        ("Ingress", "confidential-s3-console"),
        ("Ingress", "confidential-s3-gateway"),
        ("Job", "confidential-s3-bootstrap"),
        ("PodDisruptionBudget", "confidential-s3-postgresql"),
        ("Secret", "confidential-s3"),
        ("Service", "confidential-s3-api"),
        ("Service", "confidential-s3-console"),
        ("Service", "confidential-s3-garage"),
        ("Service", "confidential-s3-gateway"),
        ("Service", "confidential-s3-postgresql"),
        ("Service", "confidential-s3-postgresql-hl"),
        ("ServiceAccount", "confidential-s3-postgresql"),
        ("StatefulSet", "confidential-s3-garage"),
        ("StatefulSet", "confidential-s3-postgresql"),
    },
}


def main(chart: str, values: str, key: str | None = None) -> int:
    rendered = subprocess.run(
        ["helm", "template", "release", f"charts/{chart}", "--namespace", chart, "--values", values],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    found = {
        (doc["kind"], doc["metadata"]["name"])
        for doc in yaml.safe_load_all(rendered)
        if doc
    }
    expected = EXPECTED[key or chart]

    missing = sorted(expected - found)
    extra = sorted(found - expected)
    for kind, name in missing:
        print(f"  FAIL  {chart} did not render {kind}/{name}")
    for kind, name in extra:
        print(f"  FAIL  {chart} rendered {kind}/{name}, which is not in the expected inventory")
    if missing or extra:
        return 1

    print(f"  ok    {len(found)} objects, each its own document")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
