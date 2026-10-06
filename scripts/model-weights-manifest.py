#!/usr/bin/env python3
"""Build the weights manifest a model-serving listing pins its weights with.

    scripts/model-weights-manifest.py Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8

Prints the YAML block that goes into a listing's
`components[].deployment.values.base.model.weights`: the repository, the commit
the manifest was built from, and every file with its size and sha256.

Where the hashes come from, and why this is cheap: Hugging Face stores large
files in LFS and its tree API reports each one's `lfs.oid`, which *is* the
sha256 of the content. So the 29 GiB of a large checkpoint are pinned without
downloading them. The small files — configs, tokenizer, chat template — are not
LFS, so those are fetched and hashed here; the script refuses to fetch anything
over 64 MiB, which is what stops a repository layout change from turning this
into a silent multi-gigabyte download.

Nothing about this is Hugging Face-specific at deploy time: the manifest names a
repo path and hashes, and the chart's fetcher will take them from any mirror
that serves the same bytes (`model.weights.endpoint`). The hashes are what makes
a mirror safe to use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request

UA = "swarm-marketplace-catalog/model-weights-manifest"

# What vLLM needs to load a model, and nothing else. Excluding the rest is not
# tidiness: every file in the manifest is a file the deployment downloads before
# it can answer, and `consolidated.safetensors` or a duplicate `original/`
# directory can double that for nothing.
KEEP_SUFFIXES = (".safetensors", ".json", ".txt", ".jinja", ".model")
SKIP_EXACT = {
    ".gitattributes",
    "LICENSE",
    "LICENSE.txt",
    "NOTICE",
    "README.md",
    "USE_POLICY.md",
}
SKIP_PREFIXES = ("original/", "onnx/", "coreml/", ".cache/")
MAX_HASH_LOCALLY = 64 * 1024 * 1024


def get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def wanted(path: str) -> bool:
    if path in SKIP_EXACT or path.startswith(SKIP_PREFIXES):
        return False
    return path.endswith(KEEP_SUFFIXES)


def build(repo: str, endpoint: str, revision: str | None) -> dict:
    endpoint = endpoint.rstrip("/")
    if revision is None:
        info = get_json(f"{endpoint}/api/models/{repo}")
        revision = info["sha"]  # type: ignore[index]
    tree = get_json(f"{endpoint}/api/models/{repo}/tree/{revision}?recursive=1")

    files = []
    for entry in tree:  # type: ignore[union-attr]
        if entry["type"] != "file" or not wanted(entry["path"]):
            continue
        path, size = entry["path"], entry["size"]
        sha256 = (entry.get("lfs") or {}).get("oid")
        if not sha256:
            if size > MAX_HASH_LOCALLY:
                raise SystemExit(
                    f"{repo}:{path} is {size} bytes and is not an LFS file, so its hash is "
                    f"not in the API. Refusing to download it to find out — check the "
                    f"repository layout."
                )
            print(f"hashing {path} ({size} B)", file=sys.stderr)
            sha256 = hashlib.sha256(
                get_bytes(f"{endpoint}/{repo}/resolve/{revision}/{path}")
            ).hexdigest()
        files.append({"path": path, "size": size, "sha256": sha256})

    if not any(f["path"].endswith(".safetensors") for f in files):
        raise SystemExit(f"{repo}@{revision} has no .safetensors file — wrong repository?")

    files.sort(key=lambda f: f["path"])
    return {
        "repo": repo,
        "revision": revision,
        "totalBytes": sum(f["size"] for f in files),
        "files": files,
    }


def as_yaml(manifest: dict, indent: int) -> str:
    pad = " " * indent
    out = [
        f"{pad}repo: {manifest['repo']}",
        f"{pad}revision: {manifest['revision']}",
        f"{pad}totalBytes: {manifest['totalBytes']}",
        f"{pad}files:",
    ]
    for f in manifest["files"]:
        out.append(f"{pad}  - path: {f['path']}")
        out.append(f"{pad}    size: {f['size']}")
        out.append(f"{pad}    sha256: {f['sha256']}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="e.g. unsloth/Llama-3.2-3B-Instruct")
    parser.add_argument("--revision", help="commit sha; the branch head by default")
    parser.add_argument("--endpoint", default="https://huggingface.co")
    parser.add_argument("--indent", type=int, default=10, help="YAML indent for the block")
    parser.add_argument("--json", action="store_true", help="print JSON instead of YAML")
    args = parser.parse_args()

    manifest = build(args.repo, args.endpoint, args.revision)
    print(
        f"# {manifest['repo']}@{manifest['revision']}: {len(manifest['files'])} files, "
        f"{manifest['totalBytes'] / 2**30:.2f} GiB",
        file=sys.stderr,
    )
    print(json.dumps(manifest, indent=2) if args.json else as_yaml(manifest, args.indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
