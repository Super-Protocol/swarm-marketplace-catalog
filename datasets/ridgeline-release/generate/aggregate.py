#!/usr/bin/env python3
"""Build this release from the practice's own corpus.

The release builder is shared — one shape of release for all three networks —
and lives in `datasets/gi-network-generator/aggregate.py`. This file is the
documented path to it for THIS release, with `--network ridgeline` already chosen,
so the command stays inside the dataset directory it belongs to.

    # first the corpus, from the identifying dataset's own generator:
    (cd ../../ridgeline-endoscopy/generate && ./generate.py --out ./out)
    # then the counts over it:
    ./aggregate.py --corpus ../../ridgeline-endoscopy/generate/out --out ./out

It needs a corpus to count, which comes from the identifying dataset's own
generator; this release is derived from that corpus and never generated beside
it. Everything `datasets/gi-network-generator/aggregate.py` accepts is accepted
here, and passing `--network` again is refused rather than silently counting
somebody else's practice into this directory.
"""

from __future__ import annotations

import os
import sys

NETWORK = "ridgeline"

BUILDER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "gi-network-generator",
)
sys.path.insert(0, BUILDER)

import aggregate  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    if "--network" in argv:
        raise SystemExit(
            f"this is the {NETWORK} release builder; run "
            f"datasets/gi-network-generator/aggregate.py directly to choose another network"
        )
    return aggregate.main(["--network", NETWORK, *argv])


if __name__ == "__main__":
    raise SystemExit(main())
