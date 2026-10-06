#!/usr/bin/env python3
"""The fetcher's refusals, tested rather than read.

`charts/swarm-model-server/files/fetch-weights.py` is the only thing between a
weights manifest and the GPU: it is what makes "the evidence names the bytes this
deployment may serve" true at runtime rather than aspirational. So the paths it
must refuse are exercised here, against the same file the chart renders into its
ConfigMap.

Nothing here touches the network. Every case is rejected before a download is
attempted, which is the property under test.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
FETCHER = ROOT / "charts" / "swarm-model-server" / "files" / "fetch-weights.py"

# A path a manifest must not be able to name, and why it matters. Each one would
# write outside the weights directory, or over the readiness marker the engine's
# start depends on.
REFUSED = [
    ("..", "a bare parent reference, which has no separator to notice"),
    ("../escape.safetensors", "a parent reference in a relative path"),
    ("/etc/passwd", "an absolute path"),
    ("nested/../../escape.safetensors", "a parent reference below a directory"),
    (".", "the directory itself"),
    ("a//b.safetensors", "an empty component"),
    ("sub/./b.safetensors", "a current-directory component"),
]


def load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_weights", FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(fetcher, path: str, work: pathlib.Path) -> tuple[int, str]:
    manifest = {
        "repo": "example/model",
        "revision": "0" * 40,
        "totalBytes": 1,
        "files": [{"path": path, "size": 1, "sha256": "0" * 64}],
    }
    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    target = work / "weights"
    os.environ["WEIGHTS_MANIFEST"] = str(manifest_path)
    os.environ["WEIGHTS_DIR"] = str(target)
    # A URL that would fail loudly if the guard ever let a path through to a
    # download, so a pass here cannot be a network success.
    os.environ["HF_ENDPOINT"] = "https://127.0.0.1:1"
    # The exit code alone does not discriminate: a path that slips past the guard
    # also fails, on the unreachable endpoint above, several seconds later. What
    # is under test is that the path is *refused*, so the log is the assertion.
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = fetcher.main()
    return code, captured.getvalue()


def main() -> int:
    fetcher = load_fetcher()
    failures = []
    for path, why in REFUSED:
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            code, log = run(fetcher, path, work)
            if code == 0:
                failures.append(f"accepted {path!r} ({why})")
                continue
            if "refusing suspicious manifest path" not in log:
                failures.append(
                    f"{path!r} ({why}) failed, but not by being refused — it reached the "
                    f"download: {log.strip().splitlines()[-1:]}")
                continue
            ready = work / "weights" / ".weights-ready"
            if ready.exists():
                failures.append(f"{path!r} left the volume marked ready")
            # Nothing may have been written outside the weights directory.
            strays = [p for p in work.iterdir() if p.name not in ("manifest.json", "weights")]
            if strays:
                failures.append(f"{path!r} wrote outside the weights directory: {strays}")

    for message in failures:
        print(f"  FAIL  {message}")
    if failures:
        return 1
    print(f"{len(REFUSED)} unsafe manifest paths refused before any download")
    return 0


if __name__ == "__main__":
    sys.exit(main())
