#!/usr/bin/env python3
"""Checks that a definition whose content changed also changed its version.

A marketplace is seeded from this repository by publishing every definition it
names, and a listing already published at the same version is left as it is —
that guard is what makes seeding re-runnable. It also means "same version" has
to mean "same definition": when a definition changes under a version that some
marketplace already published, the change never reaches it, and the seeder
reports success while leaving the old one in place (SUP-108).

Git is the only place that knows what a definition said before, so this check
compares the working tree against a baseline revision — the pull request's base
in CI, `origin/main` otherwise:

    BASE_REF=<sha-or-branch> apps/tests/versions.py

The comparison is of the parsed document, not the file: reformatting a block or
rewriting a comment changes the file without changing what is published, and
failing on that would teach everyone to bump a version to silence a check.

Charts under charts/ follow the same discipline against a published index; this
is that rule for the definitions those charts are pinned by.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
FALLBACK_REFS = ("origin/main", "main")


def git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def resolve(ref: str) -> str | None:
    """The commit `ref` names, or None when this checkout does not have it."""
    try:
        return git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip() or None
    except subprocess.CalledProcessError:
        return None


def baseline() -> tuple[str | None, str]:
    """The revision to compare against, and how it was chosen."""
    requested = os.environ.get("BASE_REF")
    if requested:
        # Explicit means CI: a baseline that cannot be resolved is a broken
        # checkout (a shallow clone, usually), not a reason to skip the check.
        commit = resolve(requested)
        if not commit:
            print(f"  FAIL  BASE_REF={requested} is not a commit in this checkout")
            print("        CI needs actions/checkout with fetch-depth: 0 for this comparison")
            sys.exit(1)
        return commit, requested

    for ref in FALLBACK_REFS:
        commit = resolve(ref)
        if commit:
            return commit, ref
    return None, ""


def committed(commit: str, path: str) -> str | None:
    """The file's content at `commit`, or None when it did not exist there."""
    try:
        return git("show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return None


def canonical(document: str) -> str:
    """What the document says, independent of how it is written down."""
    # `default=str` for what YAML types and JSON does not: an unquoted date or timestamp parses to
    # a datetime, and comparing two definitions should not end in a traceback about one.
    return json.dumps(yaml.safe_load(document), sort_keys=True, default=str)


def version_of(document: str) -> str | None:
    parsed = yaml.safe_load(document) or {}
    version = (parsed.get("metadata") or {}).get("version")
    return None if version is None else str(version)


def precedence(version: str) -> tuple[int, ...] | None:
    """A comparable release number, or None for anything that is not one."""
    release = version.split("-", 1)[0].split("+", 1)[0]
    parts = release.split(".")
    if not all(part.isdigit() for part in parts) or not parts:
        return None
    return tuple(int(part) for part in parts)


def check(before: str, after: str) -> list[str]:
    if canonical(before) == canonical(after):
        return []

    was, now = version_of(before), version_of(after)
    if was is None or now is None:
        # A definition that declares no metadata.version is published under a
        # number the marketplace counts out for itself, one per submission, so
        # a change to it always reaches a re-seed. Nothing to hold it to here.
        return []

    if now == was:
        return [
            f"content changed but metadata.version is still {now} — every marketplace already "
            f"seeded at {now} keeps the old definition, and its seeder reports success",
            "bump metadata.version so the change is a version the seeder publishes",
        ]

    order_was, order_now = precedence(was), precedence(now)
    if order_was and order_now and order_now < order_was:
        return [
            f"metadata.version went backwards, {was} -> {now}",
            f"a marketplace seeded at {was} keeps that definition; publishing over it needs a "
            "higher version",
        ]

    return []


def main() -> int:
    commit, ref = baseline()
    if not commit:
        print(f"  note  no baseline revision ({' or '.join(FALLBACK_REFS)}), version bumps unchecked")
        return 0

    print(f"  note  against {ref} @ {commit[:7]}")

    failures = 0
    for definition in sorted(ROOT.glob("apps/*/app.yaml")):
        relative = definition.relative_to(ROOT).as_posix()
        before = committed(commit, relative)
        if before is None:
            print(f"  ok    {relative} (new)")
            continue

        problems = check(before, definition.read_text())
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
