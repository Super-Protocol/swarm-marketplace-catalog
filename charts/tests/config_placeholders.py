#!/usr/bin/env python3
"""
Every `${…}` in the rendered `router.yaml` has something that fills it, and nothing fills
something the file does not ask for.

`router.yaml` is attested, so neither a secret nor a hostname is written into it: both are
`${VAR}` placeholders the router's config loader substitutes from the process environment
(`libs/server-common/src/lib/config/env-placeholders.ts`). A placeholder with no value and
no default does not degrade — it throws before the first HTTP listener, so the whole
deployment is a crash loop, and nothing in a render or a golden diff would have said so.

Which makes a renamed variable the one mistake this split is exposed to. So: pull the
placeholders out of the rendered config, pull the variables the pod is actually given out
of the rendered objects, and require the two sets to agree.

Checked in both directions. An unfilled placeholder is a deployment that never starts; a
variable nothing refers to is dead configuration, and in the public ConfigMap's case it is
also a field excluded from the evidence snapshot for no reason at all.

    charts/tests/config_placeholders.py confidential-router-api charts/tests/cases/api-campaign.yaml
"""

import re
import subprocess
import sys

import yaml

# The same grammar the loader implements: `${VAR}` and `${VAR:-default}`.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}")


def render(chart: str, values: str) -> list[dict]:
    manifests = subprocess.run(
        ["helm", "template", "cr", f"charts/{chart}", "--namespace", "confidential-router", "--values", values],
        capture_output=True, text=True, check=True,
    ).stdout
    return [document for document in yaml.safe_load_all(manifests) if document]


def names_the_config(container: dict) -> bool:
    """Whether this container is pointed at `router.yaml` — the only ones the placeholders are
    a contract with."""
    return any(entry["name"] == "CR_API_CONFIG_FILE" for entry in container.get("env", []))


def main(chart: str, values: str) -> int:
    documents = render(chart, values)
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in documents}

    config = by_name[("ConfigMap", chart)]["data"]["router.yaml"]
    public = by_name[("ConfigMap", f"{chart}-public")]["data"]

    required = {name for name, default in PLACEHOLDER.findall(config) if not default}
    optional = {name for name, default in PLACEHOLDER.findall(config) if default}

    deployment = by_name[("Deployment", chart)]
    failures = 0

    pod = deployment["spec"]["template"]["spec"]
    # Only the containers that load `router.yaml`, named by the one thing that says
    # so: `CR_API_CONFIG_FILE`. Since ADR-008 the pod also runs the egress sidecar,
    # which reads a configuration of its own out of a shared volume and would fail
    # every assertion below for not needing any of it.
    readers = [c for c in pod["containers"] + pod.get("initContainers", []) if names_the_config(c)]
    if not readers:
        print(f"  FAIL  no container in {deployment['metadata']['name']} is given CR_API_CONFIG_FILE")
        return 1

    for container in readers:
        label = f"{deployment['metadata']['name']}/{container['name']}"
        from_env = {entry["name"] for entry in container.get("env", [])}
        from_config_maps = set()
        for source in container.get("envFrom", []):
            name = source["configMapRef"]["name"]
            from_config_maps |= set(by_name[("ConfigMap", name)]["data"])

        missing = sorted(required - from_env - from_config_maps)
        if missing:
            print(f"  FAIL  {label} is given no value for {', '.join(missing)}, which router.yaml requires")
            failures += 1
        else:
            print(f"  ok    {label} is given all {len(required)} required placeholder(s)")

    unused = sorted(set(public) - required - optional)
    if unused:
        print(f"  FAIL  {chart}-public carries {', '.join(unused)}, which router.yaml never refers to")
        failures += 1
    else:
        print(f"  ok    all {len(public)} key(s) of {chart}-public are referred to by router.yaml")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
