#!/usr/bin/env python3
"""Generate this dataset's corpus.

The generator itself is shared: three clinic networks come out of one program,
because the clinical model is the same specialty in the same country and only the
practice differs. It lives in `datasets/gi-network-generator/`, and this file is
the documented path to it for THIS corpus — so the command stays inside the
dataset directory it belongs to and no caller has to remember which network this
is.

    ./generate.py --out ./out
    ./generate.py --out ./out --no-documents     # tables only

Everything `datasets/gi-network-generator/generate.py` accepts is accepted here,
with `--network ridgeline` already chosen; passing `--network` again is refused rather
than silently generating somebody else's data into this directory.
"""

from __future__ import annotations

import os
import sys

NETWORK = "ridgeline"

GENERATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "gi-network-generator",
)
sys.path.insert(0, GENERATOR)

import generate  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    if "--network" in argv:
        raise SystemExit(
            f"this is the {NETWORK} generator; run "
            f"datasets/gi-network-generator/generate.py directly to choose another network"
        )
    return generate.main(["--network", NETWORK, *argv])


if __name__ == "__main__":
    raise SystemExit(main())
