#!/usr/bin/env python3
"""Fetch model weights and refuse to hand over anything the manifest did not name.

Reads a manifest (repo, revision, files[].path/.size/.sha256), downloads each file from
the Hugging Face resolve endpoint at that exact revision, and verifies the sha256 while
writing. Idempotent: a file already on disk with the right digest is left alone, so a
restarted pod does not re-download 29 GiB. Fails closed: any mismatch, any short read,
any file the manifest does not name is an error and the destination is not marked ready.

stdlib only, so the image that runs this can stay a plain python base.
"""
import hashlib, json, os, sys, time, urllib.error, urllib.request

CHUNK = 8 << 20
# How much of a file this process keeps in the page cache before handing it back
# to the kernel. See release() for why a fetcher that streams in 8 MiB chunks is
# still OOMKilled without it.
RELEASE = 64 << 20
RETRIES = 5
UA = "swarm-marketplace-weights-fetcher/1"


def log(msg: str) -> None:
    print(f"[weights] {msg}", flush=True)


def release(fd: int, offset: int, length: int, dirty: bool) -> None:
    """Hand a range of a file back to the kernel, out of the page cache.

    This is what makes the container's memory request a small number instead of
    a gamble. A container's page cache is charged to its cgroup, and the cache
    of a file being *written* is dirty until writeback completes — so a fetcher
    whose own resident set never exceeds 40 MiB still drives its cgroup to the
    limit while it writes a 4.7 GiB shard at a few hundred MiB/s, and the kernel
    OOMKills it rather than waiting for writeback. Measured on this manifest
    (Llama 3.2 3B, 6.00 GiB, 9 files) in a container with the limit named:

        512Mi   OOMKilled, cgroup peak 515 MiB   (the SUP-1 experience)
        1Gi     OOMKilled, cgroup peak 1048 MiB
        2Gi     survived, cgroup peak 2078 MiB   — i.e. the whole limit

    `POSIX_FADV_DONTNEED` drops clean pages only, so a written range is synced
    first; a range being read is already clean and is dropped as it is hashed.
    """
    if not hasattr(os, "posix_fadvise"):
        return
    if dirty:
        os.fsync(fd)
    os.posix_fadvise(fd, offset, length, os.POSIX_FADV_DONTNEED)


def digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        pending = 0
        read = 0
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            read += len(chunk)
            pending += len(chunk)
            if pending >= RELEASE:
                release(fh.fileno(), read - pending, pending, dirty=False)
                pending = 0
    return h.hexdigest()


def fetch(url: str, dest: str, expect_sha: str, expect_size: int) -> None:
    tmp = dest + ".part"
    h = hashlib.sha256()
    written = 0
    pending = 0
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(CHUNK):
            out.write(chunk)
            h.update(chunk)
            written += len(chunk)
            pending += len(chunk)
            if pending >= RELEASE:
                out.flush()
                release(out.fileno(), written - pending, pending, dirty=True)
                pending = 0
    if written != expect_size:
        os.unlink(tmp)
        raise ValueError(f"size {written} != manifest {expect_size}")
    got = h.hexdigest()
    if got != expect_sha:
        os.unlink(tmp)
        raise ValueError(f"sha256 {got} != manifest {expect_sha}")
    os.replace(tmp, dest)


def main() -> int:
    manifest_path = os.environ.get("WEIGHTS_MANIFEST", "/etc/model/manifest.json")
    target = os.environ.get("WEIGHTS_DIR", "/models/current")
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")

    with open(manifest_path) as fh:
        manifest = json.load(fh)
    repo, revision = manifest["repo"], manifest["revision"]
    source = os.environ.get("WEIGHTS_REPO", repo)

    os.makedirs(target, exist_ok=True)
    ready = os.path.join(target, ".weights-ready")
    stamp = f"{source}@{revision}"
    if os.path.exists(ready) and open(ready).read().strip() == stamp:
        log(f"already verified {stamp}")
        return 0

    total = manifest["totalBytes"]
    log(f"{stamp}: {len(manifest['files'])} files, {total / 2**30:.2f} GiB -> {target}")
    done = 0
    for entry in manifest["files"]:
        rel, sha, size = entry["path"], entry["sha256"], entry["size"]
        # Every component, not only the ones in a path with a separator in it:
        # a bare ".." has no "/" and would otherwise reach os.path.join.
        if rel.startswith("/") or any(part in ("", ".", "..") for part in rel.split("/")):
            log(f"refusing suspicious manifest path {rel!r}")
            return 1
        dest = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest) and os.path.getsize(dest) == size and digest(dest) == sha:
            log(f"ok (cached)  {rel}")
            done += size
            continue
        url = f"{endpoint}/{source}/resolve/{revision}/{rel}"
        for attempt in range(1, RETRIES + 1):
            try:
                started = time.monotonic()
                fetch(url, dest, sha, size)
                took = max(time.monotonic() - started, 1e-6)
                done += size
                log(f"ok  {rel}  {size / 2**20:.0f} MiB in {took:.0f}s "
                    f"({size / 2**20 / took:.0f} MiB/s)  [{done / total:.0%}]")
                break
            except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
                log(f"attempt {attempt}/{RETRIES} failed for {rel}: {err}")
                if attempt == RETRIES:
                    log("giving up — weights are NOT usable, failing closed")
                    return 1
                time.sleep(min(2 ** attempt, 30))

    # Nothing outside the manifest may reach the serving container.
    named = {e["path"] for e in manifest["files"]}
    for root, _dirs, names in os.walk(target):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), target)
            if rel in named or rel == ".weights-ready":
                continue
            log(f"removing unmanifested file {rel}")
            os.unlink(os.path.join(root, name))

    with open(ready, "w") as fh:
        fh.write(stamp + "\n")
    log(f"verified {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
