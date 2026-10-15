#!/usr/bin/env python3
"""Regenerate the swarm-model-server test cases from the listings.

    charts/tests/cases/model-server-cases.py
    UPDATE=1 charts/tests/run.sh          # then refresh the goldens

The values a case renders with are the values its listing produces, not a
hand-written approximation of them — otherwise a golden proves that the chart is
stable and says nothing about whether the listing still drives it correctly. So
this reads each `app.yaml`, takes `components[0].deployment.values.base`
verbatim, and fills in the three things the marketplace resolves at deploy time:
the hostname, the generated key, and the two parameters the form offers.

`model-server-two-engines` is the one case with no listing behind it: nothing
published serves two models from one card yet (SUP-332 is what makes it possible,
SUP-330 is what will ask for it). It is still built from listings rather than
written out — two real weights manifests, two real tool-calling settings, one of
them with a chat-template override — so the shared-card render is checked against
the same values a listing produces, and a listing change moves it too.

Run it after changing a listing; `charts/tests/run.sh` then shows the render
diff.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]

# case name -> (listing directory, hostname, maxModelLen, gpuMemoryUtilization)
CASES = {
    "model-server-llama": ("llama-3-2-3b-instruct", "llama.models.example", 32768, 0.85),
    "model-server-gemma": ("gemma-2-2b-it", "gemma.models.example", 8192, 0.85),
    "model-server-qwen-fp8": (
        "qwen3-coder-30b-a3b-instruct-fp8", "qwen-coder.models.example", 65536, 0.85),
}

# The one case that is not a listing: two of the models above on a single card,
# each with the share of it and the context length that an `engines` entry has to
# state for itself. The fractions add to 0.85 — under the 0.95 the chart allows,
# because the rest is the margin the driver and the second CUDA context need.
#
#   case name -> (hostname, [(engine name, listing directory, maxModelLen, share)])
SHARED = {
    "model-server-two-engines": (
        "two-models.example",
        [
            ("llama", "llama-3-2-3b-instruct", 32768, 0.55),
            ("gemma", "gemma-2-2b-it", 8192, 0.30),
        ],
    ),
}

# Both engines run in the one container that holds the card, and both manifests
# land on the one weights volume, so neither number is either listing's.
SHARED_RESOURCES = {"cpu": "8", "memory": "24Gi"}
SHARED_STORAGE = "16Gi"

# A fixed stand-in for the generated key: 32 alphanumerics, the shape the
# platform's generator emits and the shape the listings' pattern demands.
TEST_KEY = "TESTKEYtestkey0123456789abcdefAB"


def base_of(app: str) -> dict:
    definition = yaml.safe_load((ROOT / "apps" / app / "app.yaml").read_text())
    return definition["components"][0]["deployment"]["values"]["base"]


def write(case: str, values: dict, source: str) -> None:
    header = (
        f"# Generated from {source} by "
        f"charts/tests/cases/model-server-cases.py.\n"
        f"# Do not hand-edit: the point of this file is that the golden render is the\n"
        f"# render the listing produces, so a listing change shows up as a golden diff.\n"
    )
    out = ROOT / "charts" / "tests" / "cases" / f"{case}.yaml"
    out.write_text(header + yaml.safe_dump(values, sort_keys=True, width=100))
    print(f"wrote {out.relative_to(ROOT)}", file=sys.stderr)


def main() -> int:
    for case, (app, hostname, max_len, gpu_util) in CASES.items():
        definition = yaml.safe_load((ROOT / "apps" / app / "app.yaml").read_text())
        base = definition["components"][0]["deployment"]["values"]["base"]
        base["hostname"] = hostname
        base["apiKey"] = TEST_KEY
        base["model"]["maxModelLen"] = max_len
        base["model"]["gpuMemoryUtilization"] = gpu_util

        write(case, base, f"apps/{app}/app.yaml")

    for case, (hostname, entries) in SHARED.items():
        engines = []
        for name, app, max_len, gpu_util in entries:
            model = dict(base_of(app)["model"])
            model["maxModelLen"] = max_len
            model["gpuMemoryUtilization"] = gpu_util
            engines.append({"name": name, "model": model})
        write(case, {
            "hostname": hostname,
            "apiKey": TEST_KEY,
            "persistence": {"size": SHARED_STORAGE},
            "resources": dict(SHARED_RESOURCES),
            "engines": engines,
        }, " and ".join(f"apps/{app}/app.yaml" for _, app, _, _ in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
