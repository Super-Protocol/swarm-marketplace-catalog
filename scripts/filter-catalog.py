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

ENV_REF = re.compile(r"\$\{\w+\}")
LIST_ITEM = re.compile(r"^  - ")


def split_list_items(section: str) -> list[str]:
    lines = section.splitlines(keepends=True)
    items: list[list[str]] = []
    buf: list[str] = []
    preamble: list[str] = []
    started = False
    for line in lines:
        if LIST_ITEM.match(line):
            started = True
            if buf:
                items.append(buf)
            buf = [line]
            continue
        if started:
            buf.append(line)
        else:
            preamble.append(line)
    if buf:
        items.append(buf)
    # Preamble (the "apps:\n" header) is returned as a fake first piece by the caller.
    return ["".join(preamble)] + ["".join(block) for block in items]


def field(item: str, name: str) -> str | None:
    match = re.search(rf"^(?:  - |\s+){name}:\s*(.+?)\s*$", item, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def metadata_name(document: Path) -> str | None:
    if not document.is_file():
        return None
    match = re.search(r"^  name:\s*(.+)\s*$", document.read_text(), re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def app_declares_secrets(app_yaml: Path) -> bool:
    if not app_yaml.is_file():
        return False
    return bool(re.search(r"^secrets:\s*$", app_yaml.read_text(), re.MULTILINE))


def keep_app(root: Path, item: str) -> bool:
    if ENV_REF.search(item):
        return False
    path = field(item, "path")
    if not path:
        return False
    if app_declares_secrets(root / path / "app.yaml"):
        return False
    return True


def keep_dataset(item: str) -> bool:
    return ENV_REF.search(item) is None and field(item, "path") is not None


def listing_names(root: Path, items: list[str], filename: str) -> set[str]:
    names: set[str] = set()
    for item in items:
        path = field(item, "path")
        if not path:
            continue
        name = metadata_name(root / path / filename)
        if name:
            names.add(name)
    return names


def keep_grant(item: str, apps: set[str], datasets: set[str]) -> bool:
    dataset = field(item, "dataset")
    if dataset not in datasets:
        return False
    apps_line = re.search(r"apps:\s*\[([^\]]+)\]", item)
    if apps_line:
        app_names = [part.strip() for part in apps_line.group(1).split(",") if part.strip()]
        if any(name not in apps for name in app_names):
            return False
    return True


def keep_review(item: str, apps: set[str]) -> bool:
    app = field(item, "app")
    return app in apps


def rebuild_section(header_and_items: list[str]) -> str:
    header, *items = header_and_items
    return header + "".join(items)


def split_sections(text: str) -> dict[str, str]:
    keys = ("organizations", "apps", "datasets", "grants", "reviews")
    positions: list[tuple[str, int]] = []
    for key in keys:
        match = re.search(rf"^{key}:\s*$", text, re.MULTILINE)
        if match:
            positions.append((key, match.start()))
    positions.sort(key=lambda pair: pair[1])
    sections: dict[str, str] = {}
    prefix_end = positions[0][1] if positions else len(text)
    sections["_prefix"] = text[:prefix_end]
    for index, (key, start) in enumerate(positions):
        end = positions[index + 1][1] if index + 1 < len(positions) else len(text)
        sections[key] = text[start:end]
    return sections


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
    source = (root / "catalog.yaml").read_text()
    sections = split_sections(source)

    app_parts = split_list_items(sections["apps"])
    kept_app_items = [item for item in app_parts[1:] if keep_app(root, item)]
    dataset_parts = split_list_items(sections["datasets"])
    kept_dataset_items = [item for item in dataset_parts[1:] if keep_dataset(item)]

    apps = listing_names(root, kept_app_items, "app.yaml")
    datasets = listing_names(root, kept_dataset_items, "data.yaml")

    grant_parts = split_list_items(sections.get("grants", "grants:\n"))
    kept_grants = [item for item in grant_parts[1:] if keep_grant(item, apps, datasets)]

    review_parts = split_list_items(sections.get("reviews", "reviews:\n"))
    kept_reviews = [item for item in review_parts[1:] if keep_review(item, apps)]

    out = "".join(
        [
            sections["_prefix"],
            sections["organizations"],
            rebuild_section([app_parts[0], *kept_app_items]),
            rebuild_section([dataset_parts[0], *kept_dataset_items]),
            rebuild_section([grant_parts[0], *kept_grants]) if "grants" in sections else "",
            rebuild_section([review_parts[0], *kept_reviews]) if "reviews" in sections else "",
        ]
    )
    dest = args.output or (root / "catalog.yaml")
    dest.write_text(out)
    skipped_apps = len(app_parts) - 1 - len(kept_app_items)
    skipped_datasets = len(dataset_parts) - 1 - len(kept_dataset_items)
    print(
        f"[filter] kept {len(kept_app_items)} app(s), {len(kept_dataset_items)} dataset(s); "
        f"skipped {skipped_apps} app(s), {skipped_datasets} dataset(s) that need extra env/secrets",
        flush=True,
    )
    for item in kept_app_items:
        print(f"[filter] app {field(item, 'path')}", flush=True)


if __name__ == "__main__":
    main()
