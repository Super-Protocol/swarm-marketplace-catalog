#!/usr/bin/env python3
"""Rewrite catalog.yaml so seed can run without extra environment variables.

Drops:
  - app entries whose catalog secrets interpolate ${VAR}
  - app entries whose AppDefinition declares publisher secrets (those must be set before publish)
  - dataset entries whose secrets interpolate ${VAR}
  - grants / reviews that point at dropped listings
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ENV_REF = re.compile(r"\$\{\w+\}")


def contains_env(value: object) -> bool:
    if isinstance(value, str):
        return ENV_REF.search(value) is not None
    if isinstance(value, dict):
        return any(contains_env(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_env(item) for item in value)
    return False


def metadata_name(document: Path) -> str | None:
    if not document.is_file():
        return None
    parsed = yaml.safe_load(document.read_text()) or {}
    if not isinstance(parsed, dict):
        return None
    name = (parsed.get("metadata") or {}).get("name")
    return name if isinstance(name, str) and name else None


def app_declares_secrets(app_yaml: Path) -> bool:
    if not app_yaml.is_file():
        return False
    parsed = yaml.safe_load(app_yaml.read_text()) or {}
    return isinstance(parsed, dict) and "secrets" in parsed


def keep_app(root: Path, item: dict) -> bool:
    if contains_env(item):
        return False
    path = item.get("path")
    if not isinstance(path, str) or not path:
        return False
    if app_declares_secrets(root / path / "app.yaml"):
        return False
    return True


def keep_dataset(item: dict) -> bool:
    path = item.get("path")
    return isinstance(path, str) and bool(path) and not contains_env(item)


def listing_names(root: Path, items: list[dict], filename: str) -> set[str]:
    names: set[str] = set()
    for item in items:
        path = item.get("path")
        if not isinstance(path, str):
            continue
        name = metadata_name(root / path / filename)
        if name:
            names.add(name)
    return names


def keep_grant(item: dict, apps: set[str], datasets: set[str]) -> bool:
    if item.get("dataset") not in datasets:
        return False
    named = item.get("apps") or []
    if isinstance(named, list) and any(name not in apps for name in named):
        return False
    return True


def keep_review(item: dict, apps: set[str]) -> bool:
    return item.get("app") in apps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog_dir", type=Path, help="Directory that contains catalog.yaml")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Where to write the filtered catalog (default: replace catalog.yaml in a copy)",
    )
    args = parser.parse_args()
    root = args.catalog_dir.resolve()
    document = yaml.safe_load((root / "catalog.yaml").read_text()) or {}

    apps_in = [item for item in document.get("apps") or [] if isinstance(item, dict)]
    datasets_in = [item for item in document.get("datasets") or [] if isinstance(item, dict)]
    kept_apps = [item for item in apps_in if keep_app(root, item)]
    kept_datasets = [item for item in datasets_in if keep_dataset(item)]

    app_names = listing_names(root, kept_apps, "app.yaml")
    dataset_names = listing_names(root, kept_datasets, "data.yaml")

    grants_in = [item for item in document.get("grants") or [] if isinstance(item, dict)]
    reviews_in = [item for item in document.get("reviews") or [] if isinstance(item, dict)]
    document["apps"] = kept_apps
    document["datasets"] = kept_datasets
    if "grants" in document:
        document["grants"] = [item for item in grants_in if keep_grant(item, app_names, dataset_names)]
    if "reviews" in document:
        document["reviews"] = [item for item in reviews_in if keep_review(item, app_names)]

    dest = args.output or (root / "catalog.yaml")
    dest.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    skipped_apps = len(apps_in) - len(kept_apps)
    skipped_datasets = len(datasets_in) - len(kept_datasets)
    print(
        f"[filter] kept {len(kept_apps)} app(s), {len(kept_datasets)} dataset(s); "
        f"skipped {skipped_apps} app(s), {skipped_datasets} dataset(s) that need extra env/secrets",
        flush=True,
    )
    for item in kept_apps:
        print(f"[filter] app {item.get('path')}", flush=True)


if __name__ == "__main__":
    main()
