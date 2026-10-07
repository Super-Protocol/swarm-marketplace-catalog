#!/usr/bin/env python3
"""Regenerate the three swarm-model-server test cases from the listings.

    charts/tests/cases/model-server-cases.py
    UPDATE=1 charts/tests/run.sh          # then refresh the goldens

The values a case renders with are the values its listing produces, not a
hand-written approximation of them — otherwise a golden proves that the chart is
stable and says nothing about whether the listing still drives it correctly. So
this reads each `app.yaml`, takes `components[0].deployment.values.base`
verbatim, and fills in the three things the marketplace resolves at deploy time:
the hostname, the generated key, and the two parameters the form offers.

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

# A fixed stand-in for the generated key: 32 alphanumerics, the shape the
# platform's generator emits and the shape the listings' pattern demands.
TEST_KEY = "TESTKEYtestkey0123456789abcdefAB"


def main() -> int:
    for case, (app, hostname, max_len, gpu_util) in CASES.items():
        definition = yaml.safe_load((ROOT / "apps" / app / "app.yaml").read_text())
        base = definition["components"][0]["deployment"]["values"]["base"]
        base["hostname"] = hostname
        base["apiKey"] = TEST_KEY
        base["model"]["maxModelLen"] = max_len
        base["model"]["gpuMemoryUtilization"] = gpu_util

        header = (
            f"# Generated from apps/{app}/app.yaml by "
            f"charts/tests/cases/model-server-cases.py.\n"
            f"# Do not hand-edit: the point of this file is that the golden render is the\n"
            f"# render the listing produces, so a listing change shows up as a golden diff.\n"
        )
        out = ROOT / "charts" / "tests" / "cases" / f"{case}.yaml"
        out.write_text(header + yaml.safe_dump(base, sort_keys=True, width=100))
        print(f"wrote {out.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
