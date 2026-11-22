#!/usr/bin/env python3
"""Attaching a document corpus must change nothing the cloud digests.

    charts/tests/rag_binding.py

This is the property the Confidential RAG listing's `expectedDigest` rests on, and the one no
golden diff would notice — because both goldens are correct, and what matters is the
*relationship* between them.

The cloud digests the rendered namespace. A `Secret`'s values are lifted out of the deployment
bundle and sealed for the target before anything is applied, so they are not in the snapshot;
everything else is. The resolved data binding is therefore allowed to reach the Secret and
nowhere else. A patch that ever put a connection into an env value, a ConfigMap or a command
argument would make the declared digest a property of *whether somebody attached data* — and
the gate admits a deployment only when its evidence matches the declared digest, so the
deployments it refused would be exactly the ones that mounted gated data.

That failure is also invisible from either side on its own: the bound render is fine, the
unbound render is fine, every image is pinned, every object is present, and the first gated
deployment is refused with nothing anywhere to say why.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

CHART = "charts/confidential-rag"
BOUND = "charts/tests/cases/rag-stack.yaml"
UNBOUND = "charts/tests/cases/rag-stack-unbound.yaml"
SECRET = ("Secret", "confidential-rag-secrets")


def render(values: str) -> dict[tuple[str, str], dict]:
    out = subprocess.run(
        ["helm", "template", "rag", CHART, "--namespace", "confidential-rag",
         "--skip-tests", "--values", values],
        capture_output=True, text=True, check=True,
    ).stdout
    objects = {}
    for doc in yaml.safe_load_all(out):
        if doc:
            objects[(doc["kind"], doc["metadata"]["name"])] = doc
    return objects


def main() -> int:
    bound, unbound = render(BOUND), render(UNBOUND)

    if set(bound) != set(unbound):
        print("  FAIL  attaching a corpus changed the object set, not only its contents:")
        for key in sorted(set(bound) ^ set(unbound)):
            print(f"          {key[0]}/{key[1]}")
        return 1

    differing = sorted(key for key in bound if bound[key] != unbound[key])
    if differing != [SECRET]:
        print("  FAIL  attaching a corpus changed more than the Secret:")
        for kind, name in differing:
            print(f"          {kind}/{name}")
        print("        Everything the binding touches has to be in the Secret: its values are")
        print("        lifted out before the cloud takes the evidence snapshot, and anything")
        print("        else makes the declared digest depend on whether data was attached.")
        return 1

    # And the binding really is in there, rather than the two renders agreeing by both
    # having dropped it.
    binding = bound[SECRET]["stringData"].get("corpus-binding.json", "")
    if "clickhouse-http" not in binding:
        print("  FAIL  the bound render's Secret does not carry the binding — this check "
              "would pass on two renders that both lost it")
        return 1

    print(f"  ok    {len(bound)} objects, and the binding reaches the Secret and nothing else")
    return 0


if __name__ == "__main__":
    sys.exit(main())
