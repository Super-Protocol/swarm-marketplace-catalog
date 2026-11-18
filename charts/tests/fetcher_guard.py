#!/usr/bin/env python3
"""The fetcher's refusals, tested rather than read.

`charts/swarm-model-server/files/fetch-weights.py` is the only thing between a
weights manifest and the GPU: it is what makes "the evidence names the bytes this
deployment may serve" true at runtime rather than aspirational. So the paths it
must refuse are exercised here, against the same file the chart renders into its
ConfigMap.

The refusals touch no network: every case is rejected before a download is
attempted, which is the property under test. The happy path at the end does, over
loopback, because what it checks cannot be checked without a write — that the
fetcher hands each window of a written file back to the kernel as it goes. Its own
resident set was never the problem; the page cache of the file it is writing is
charged to the container's cgroup, and without the release this fetcher is
OOMKilled under a 512Mi limit and under a 1Gi one (SUP-839, and the SUP-1
experience before it).
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import threading

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



def serve(payload: bytes) -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    """A stand-in for the weights endpoint, serving one body for any path."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's name
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def fetches_and_releases(fetcher) -> list[str]:
    """The fetcher writes what the manifest names, verifies it, and keeps no cache.

    `CHUNK` and `RELEASE` are lowered for the duration so a few megabytes
    exercise the same windowing as a 4.7 GiB shard — the release is per chunk, so
    the effective window is the larger of the two. What is under test is that it
    happens at all, on the written file and on the re-read that verifies a cached
    one.
    """
    problems: list[str] = []
    payload = (b"weights" * 1024)[:1024] * 3072          # 3 MiB, incompressible enough
    sha = hashlib.sha256(payload).hexdigest()
    server, _thread = serve(payload)
    released: list[tuple[int, int]] = []
    real_fadvise = os.posix_fadvise

    def counting_fadvise(fd, offset, length, advice):
        if advice == os.POSIX_FADV_DONTNEED:
            released.append((offset, length))
        return real_fadvise(fd, offset, length, advice)

    windows = (fetcher.CHUNK, fetcher.RELEASE)
    try:
        fetcher.CHUNK, fetcher.RELEASE = 256 << 10, 1 << 20
        os.posix_fadvise = counting_fadvise
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            target = work / "weights"
            target.mkdir()
            # Something the manifest does not name, which the fetcher prunes
            # before it marks the volume ready.
            (target / "unmanifested.safetensors").write_bytes(b"not in the manifest")
            manifest = {
                "repo": "example/model",
                "revision": "0" * 40,
                "totalBytes": len(payload),
                "files": [{"path": "model.safetensors", "size": len(payload), "sha256": sha}],
            }
            (work / "manifest.json").write_text(json.dumps(manifest))
            os.environ["WEIGHTS_MANIFEST"] = str(work / "manifest.json")
            os.environ["WEIGHTS_DIR"] = str(target)
            os.environ["HF_ENDPOINT"] = f"http://127.0.0.1:{server.server_address[1]}"

            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = fetcher.main()
            if code != 0:
                return [f"a manifest it should have satisfied failed: {captured.getvalue()}"]
            if not (target / ".weights-ready").exists():
                problems.append("the volume was not marked ready")
            if (target / "model.safetensors").read_bytes() != payload:
                problems.append("the file on the volume is not what was served")
            if (target / "unmanifested.safetensors").exists():
                problems.append("a file the manifest does not name survived")
            written = len(released)
            if written < 3:
                problems.append(f"a {len(payload) >> 20} MiB write released {written} window(s) "
                                f"of page cache at a {fetcher.RELEASE >> 20} MiB window: the "
                                f"container's memory limit is sized on this happening")
            if sum(length for _offset, length in released) < len(payload) // 2:
                problems.append("less than half of what was written was released")

            # The restart path: the file is already there, so it is re-hashed
            # rather than downloaded, and that read is cache too.
            (target / ".weights-ready").unlink()
            released.clear()
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = fetcher.main()
            if code != 0 or "cached" not in captured.getvalue():
                problems.append(f"the cached file was not recognised: {captured.getvalue()}")
            if len(released) < 2:
                problems.append(f"re-hashing a cached file released {len(released)} window(s) "
                                f"of page cache")

            # And the whole point of the hashes: a file that is not what the
            # manifest says must not reach the engine.
            (target / ".weights-ready").unlink()
            (target / "model.safetensors").write_bytes(payload[:-1] + b"X")
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = fetcher.main()
            if code != 0:
                problems.append("a corrupted file was refused by failing the download, not the "
                                "hash — the re-fetch was expected to repair it")
            elif (target / "model.safetensors").read_bytes() != payload:
                problems.append("a corrupted file was left on the volume and the engine started")
    finally:
        os.posix_fadvise = real_fadvise
        fetcher.CHUNK, fetcher.RELEASE = windows
        server.shutdown()
    return problems


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

    happy = fetches_and_releases(fetcher)
    for message in failures + happy:
        print(f"  FAIL  {message}")
    if failures or happy:
        return 1
    print(f"{len(REFUSED)} unsafe manifest paths refused before any download, "
          "and a verified fetch writes, prunes, re-hashes and keeps no page cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
