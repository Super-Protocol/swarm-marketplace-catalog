#!/usr/bin/env python3
"""
Every image a chart renders is declared by the listing that deploys it, with the
same digest.

A component's `images:` block is an allow-list and the render path is fail-closed
on it: an image in the manifests that is not in the list refuses the whole
deployment (marketplace specification §2.7). Nothing in this repository notices,
because `helm template` neither reads the listing nor cares — which is exactly
the shape of the mistake this exists for. A second container added to a chart
renders cleanly here, passes every golden, and then refuses to deploy at all;
so does a digest bumped in one file and not the other, and that one is worse,
because the listing then advertises a pin the cluster never pulls.

Subchart images count. The marketplace renders the whole chart and reads every
image out of the result, which is why the listing declares Spilo, etcd, busybox
and HAProxy even though no template in this repository writes them.

    charts/tests/declared_images.py confidential-router router-api \\
        confidential-router-api charts/tests/cases/api-one-model.yaml
"""

import subprocess
import sys

import yaml


def rendered_images(chart: str, values: str) -> set[str]:
    output = subprocess.run(
        ["helm", "template", "cr", f"charts/{chart}", "--namespace", "confidential-router", "--values", values],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    images: set[str] = set()
    for document in yaml.safe_load_all(output):
        images |= images_in(document)
    return images


def images_in(node: object) -> set[str]:
    """Every `image:` string anywhere in a manifest — containers, init containers and whatever
    a subchart invents, because the render path reads them all."""
    if isinstance(node, dict):
        found = {node["image"]} if isinstance(node.get("image"), str) else set()
        return found.union(*(images_in(value) for value in node.values())) if node else found
    if isinstance(node, list):
        return set().union(*(images_in(item) for item in node)) if node else set()
    return set()


def declared(listing: str, component: str) -> dict[str, str]:
    definition = yaml.safe_load(open(f"apps/{listing}/app.yaml"))
    for candidate in definition["components"]:
        if candidate["name"] == component:
            return {image["name"]: image["digest"] for image in candidate.get("images", [])}
    raise SystemExit(f"  FAIL  apps/{listing}/app.yaml has no component {component!r}")


def main(listing: str, component: str, chart: str, values: str) -> int:
    allowed = declared(listing, component)
    failures = 0

    for reference in sorted(rendered_images(chart, values)):
        # A tag rather than a digest is the listing's to rewrite, and it declares
        # the name for exactly that: `ollama/ollama` is a tag in the vendor's
        # chart and a digest in the deployment.
        name, _, digest = reference.partition("@")
        name = name.rsplit(":", 1)[0] if not digest and ":" in name.rsplit("/", 1)[-1] else name

        if name not in allowed:
            print(f"  FAIL  {chart} renders {name}, which apps/{listing}/app.yaml does not declare for {component}")
            failures += 1
        elif digest and digest != allowed[name]:
            print(f"  FAIL  {name}: the chart pulls {digest[:19]}… and the listing declares {allowed[name][:19]}…")
            failures += 1
        else:
            print(f"  ok    {name} @ {(digest or allowed[name])[:19]}…")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
