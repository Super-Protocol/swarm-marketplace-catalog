#!/usr/bin/env python3
"""Checks the two ways a schema-valid AppDefinition still fails at deploy time.

1. `op: replace` requires its path to exist in the values it is patching. The
   platform asserts this while rendering, which means the mistake surfaces on
   somebody's deployment rather than in review; here it is a failed check.
2. A `base` key the target chart has no value for is silently ignored by Helm.
   That is the failure that deploys cleanly and configures nothing, so for the
   charts that live in this repository every base leaf is checked against the
   chart's own values.yaml.

Charts pulled from a third-party repository are out of reach of (2) — their
values are not in this tree — so only (1) applies to them. So is anything under a
subchart the chart depends on and does not itself override: those values belong
to the dependency's schema, not to this repository's.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
OWN_REPO = "https://super-protocol.github.io/swarm-marketplace-catalog/charts"


def leaves(node, prefix=""):
    """Every dotted path in `node` that holds something other than a mapping."""
    if isinstance(node, dict) and node:
        for key, child in node.items():
            yield from leaves(child, f"{prefix}.{key}" if prefix else str(key))
    elif prefix:
        yield prefix


def known(values, path: str) -> bool:
    """Whether a chart declares `path`, treating an empty mapping as free-form."""
    node = values
    for segment in path.split("."):
        if not isinstance(node, dict):
            # The chart declared a scalar or a list here; anything below it is
            # the caller reshaping a value the chart does know about.
            return True
        if node == {}:
            # `podAnnotations: {}` and friends: a map the chart fills from
            # whatever it is given.
            return True
        if segment not in node:
            return False
        node = node[segment]
    return True


def subchart_names(chart: pathlib.Path) -> set[str]:
    """Dependency names and aliases, whose values live in the subchart, not here."""
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text()) or {}
    names = {"global"}
    for dependency in metadata.get("dependencies") or []:
        names.add(dependency.get("alias") or dependency.get("name"))
    return names


def check(definition: pathlib.Path) -> list[str]:
    app = yaml.safe_load(definition.read_text())
    problems: list[str] = []

    for component in app.get("components", []):
        name = component["name"]
        values = component["deployment"]["values"]
        base = values.get("base") or {}
        patches = values.get("patches") or []

        source = component.get("source", {})
        chart = ROOT / "charts" / source.get("chart", "")
        chart_values_file = chart / "values.yaml"
        if source.get("repoUrl") == OWN_REPO and chart_values_file.is_file():
            chart_values = yaml.safe_load(chart_values_file.read_text()) or {}
            subcharts = subchart_names(chart)
            for path in leaves(base):
                if path.split(".")[0] in subcharts:
                    continue
                if not known(chart_values, path):
                    problems.append(
                        f"{name}: base sets {path}, which {chart.name}/values.yaml does not declare"
                    )

        # A `replace` may rely on a key an earlier `add` created, so the reachable
        # set grows as the list is walked.
        reachable = set(leaves(base))
        for index, patch in enumerate(patches):
            path = patch.get("path", "")
            if patch.get("op") == "replace":
                # Either the base names this exact path, or it names something
                # inside it (a mapping the base filled in), or it names a list or
                # scalar this path indexes into.
                covered = path in reachable or any(
                    existing.startswith(f"{path}.") or path.startswith(f"{existing}.")
                    for existing in reachable
                )
                if not covered:
                    problems.append(
                        f"{name}: patches[{index}] replaces {path}, which nothing before it "
                        f"defines — use `add` to create it"
                    )
            if patch.get("op") in ("add", "replace"):
                reachable.add(path)

    return problems


def main() -> int:
    failures = 0
    for definition in sorted(ROOT.glob("apps/*/app.yaml")):
        relative = definition.relative_to(ROOT)
        problems = check(definition)
        if problems:
            failures += 1
            print(f"  FAIL  {relative}")
            for problem in problems:
                print(f"        {problem}")
        else:
            print(f"  ok    {relative}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
