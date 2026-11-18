#!/usr/bin/env python3
"""
Every vetted model this chart carries, checked against what a preset claims to be.

`model.preset: <slug>` is how a listing whose model is a *question put to a
person* answers it: the slug is a parameter's value, and everything behind it —
the weights pinned file by file, the tool-call parser, the licence — comes from
`charts/swarm-model-server/files/models/<slug>.yaml`. That makes a preset the
place where "the choice is constrained to weights whose licence permits
commercial self-hosting" is either true or merely asserted, so:

  * the licence is recorded, with an identifier, a URL, and
    `commercialSelfHosting: true` — the chart refuses a render otherwise, but a
    render nobody writes refuses nothing;
  * the weights are pinned the way the chart demands, at a commit rather than a
    branch, with a sha256 per file and a `totalBytes` that adds up;
  * `sizing` and `measured` are present, because a preset's whole value to a
    composing listing is that somebody already ran the model and wrote down what
    it needed;
  * a tool-call parser, if one is named, is one vLLM registers — the chart
    checks this too, but only for the preset a case happens to render;
  * and where a listing in this repository serves the same model, the two pins
    are the same bytes. A preset and a listing drifting apart would mean the
    component and the single-model listing serving different weights under one
    model id, which nothing else here would notice.

    charts/tests/model_presets.py
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRESETS = ROOT / "charts/swarm-model-server/files/models"
CHART = ROOT / "charts/swarm-model-server"

# The listings that pin the same model themselves. They predate the presets and
# are frozen at the chart version they publish, so this is a drift check, not a
# deduplication: if either side is regenerated, the other is a decision.
LISTINGS = {
    "llama-3.2-3b-instruct": "llama-3-2-3b-instruct",
    "gemma-2-2b-it": "gemma-2-2b-it",
    "qwen3-coder-30b-a3b-instruct-fp8": "qwen3-coder-30b-a3b-instruct-fp8",
}

STATUSES = ("measured", "render-only")


def tool_parsers() -> set[str]:
    """The parser names the chart's own list admits, read from the helper."""
    helpers = (CHART / "templates/_helpers.tpl").read_text()
    block = helpers.split('define "swarm-model-server.toolParsers"')[1].split("{{- end -}}")[0]
    return set(re.findall(r'"([a-z0-9_]+)"', block))


def listing_weights(listing: str) -> dict | None:
    definition = yaml.safe_load((ROOT / "apps" / listing / "app.yaml").read_text())
    for component in definition.get("components", []):
        model = component.get("deployment", {}).get("values", {}).get("base", {}).get("model")
        if model and model.get("weights"):
            return model["weights"]
    return None


def check(slug: str, preset: dict, parsers: set[str]) -> list[str]:
    problems = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    need(preset.get("id") == slug,
         f"id {preset.get('id')!r} is not the slug: the file name is what `model.preset` names")
    need(bool(preset.get("title")), "no title")
    need(bool(preset.get("source")), "no source repository")
    need(preset.get("status") in STATUSES,
         f"status {preset.get('status')!r} is not one of {', '.join(STATUSES)}")

    licence = preset.get("licence") or {}
    need(bool(licence.get("spdx")), "the licence has no identifier")
    need(bool(licence.get("name")), "the licence has no name")
    need(str(licence.get("url", "")).startswith("http"), "the licence has no URL")
    need(licence.get("commercialSelfHosting") is True,
         "the licence does not record that commercial self-hosting is permitted, which is the "
         "whole admission criterion for a preset")
    need(bool(licence.get("conditions")),
         "the licence names no conditions — Apache-2.0 has two, and a model licence that "
         "truly has none is worth saying so explicitly")

    weights = preset.get("weights") or {}
    need(bool(weights.get("repo")), "the weights name no repository")
    need(bool(re.fullmatch(r"[0-9a-f]{40}", str(weights.get("revision", "")))),
         f"revision {weights.get('revision')!r} is not a 40-character commit sha: a branch moves")
    files = weights.get("files") or []
    need(bool(files), "the weights manifest is empty")
    for entry in files:
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            problems.append(f"{entry.get('path')} has no sha256")
        if not entry.get("size"):
            problems.append(f"{entry.get('path')} has no size")
    total = sum(int(entry.get("size", 0)) for entry in files)
    need(total == int(weights.get("totalBytes", 0)),
         f"totalBytes is {weights.get('totalBytes')} and the files add up to {total}")

    sizing = preset.get("sizing") or {}
    need(bool(sizing.get("maxModelLen")), "sizing names no context length")
    need(bool(sizing.get("gpuMemoryUtilization")), "sizing names no share of the card")
    need(bool(sizing.get("minVram")), "sizing names no smallest card")
    need(bool((sizing.get("resources") or {}).get("memory")),
         "sizing names no container memory, which is the number a composing listing's quota is "
         "summed from")

    if preset.get("status") == "measured":
        measured = preset.get("measured") or {}
        need(bool(measured.get("card")), "status is `measured` and no card is named")
        need(bool(measured.get("engine")), "status is `measured` and no engine version is named")
        need(bool(measured.get("singleStreamTokensPerSecond")),
             "status is `measured` and no throughput is recorded — an unmeasured regression is "
             "the one outcome this epic does not accept")

    calling = preset.get("toolCalling") or {}
    if calling.get("enabled"):
        need(calling.get("parser") in parsers,
             f"tool-call parser {calling.get('parser')!r} is not one the chart admits")
    else:
        need(not calling.get("parser"),
             "a tool-call parser is named and tool calling is off")

    template = preset.get("chatTemplateFile")
    if template:
        need((CHART / "files/chat-templates" / template).exists(),
             f"chatTemplateFile {template!r} is not in the chart")

    return problems


def main() -> int:
    parsers = tool_parsers()
    presets = sorted(PRESETS.glob("*.yaml"))
    if not presets:
        print("  FAIL  no presets at all: `model.preset` would accept nothing")
        return 1

    failures = 0
    for path in presets:
        slug = path.stem
        preset = yaml.safe_load(path.read_text())
        problems = check(slug, preset, parsers)
        for problem in problems:
            print(f"  FAIL  {slug}: {problem}")
        failures += len(problems)
        if not problems:
            licence = preset["licence"]
            weights = preset["weights"]
            print(f"  ok    {slug}: {licence['spdx']}, commercial self-hosting permitted, "
                  f"{len(weights['files'])} files pinned, "
                  f"{weights['totalBytes'] / 2 ** 30:.2f} GiB, {preset['status']}")

        listing = LISTINGS.get(slug)
        if listing is None:
            continue
        theirs = listing_weights(listing)
        if theirs is None:
            print(f"  FAIL  {slug}: apps/{listing}/app.yaml pins no weights to compare against")
            failures += 1
        elif (theirs.get("repo"), theirs.get("revision")) != (
                preset["weights"].get("repo"), preset["weights"].get("revision")):
            print(f"  FAIL  {slug}: the preset pins {preset['weights'].get('repo')}@"
                  f"{str(preset['weights'].get('revision'))[:12]} and apps/{listing} pins "
                  f"{theirs.get('repo')}@{str(theirs.get('revision'))[:12]} — one of them moved")
            failures += 1
        elif theirs.get("files") != preset["weights"].get("files"):
            print(f"  FAIL  {slug}: the preset and apps/{listing} pin the same commit and "
                  f"different files")
            failures += 1
        else:
            print(f"  ok    {slug}: the same bytes as apps/{listing}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
