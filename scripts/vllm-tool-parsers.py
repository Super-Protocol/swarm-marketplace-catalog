#!/usr/bin/env python3
"""Print the tool-call parser names a vLLM release registers.

    scripts/vllm-tool-parsers.py v0.30.0

The list belongs in `charts/swarm-model-server/templates/_helpers.tpl`
(`swarm-model-server.toolParsers`), which is what refuses a misspelled parser at
render time instead of at pod start. Regenerate it whenever the chart's
`appVersion` moves: parsers are added and renamed between releases, and a
listing pinning a name that no longer exists fails on deploy.

Reads the registry source straight from GitHub, so it needs no vLLM install.
"""

from __future__ import annotations

import re
import sys
import urllib.request

URL = (
    "https://raw.githubusercontent.com/vllm-project/vllm/{ref}/"
    "vllm/tool_parsers/__init__.py"
)
# Releases before the registry moved kept it here.
LEGACY_URL = (
    "https://raw.githubusercontent.com/vllm-project/vllm/{ref}/"
    "vllm/entrypoints/openai/tool_parsers/__init__.py"
)


def fetch(ref: str) -> str:
    last: Exception | None = None
    for template in (URL, LEGACY_URL):
        try:
            with urllib.request.urlopen(template.format(ref=ref), timeout=60) as response:
                return response.read().decode()
        except Exception as error:  # noqa: BLE001 - either path may 404
            last = error
    raise SystemExit(f"could not read the parser registry at {ref}: {last}")


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    source = fetch(ref)

    names = set(re.findall(r'register_lazy_module\(\s*name="([a-z0-9_]+)"', source))
    block = re.search(r"_TOOL_PARSERS_TO_REGISTER\s*=\s*\{(.*?)\n\}", source, re.S)
    if block:
        names |= set(re.findall(r'^\s{4}"([a-z0-9_]+)":', block.group(1), re.M))
    names |= set(re.findall(r'@ToolParserManager\.register_module\(\["?([a-z0-9_]+)', source))

    if not names:
        raise SystemExit(f"no parser names found at {ref} — the registry moved again")

    ordered = sorted(names)
    print(f"# {len(ordered)} parsers at {ref}", file=sys.stderr)
    for i in range(0, len(ordered), 6):
        print("  " + " ".join(f'"{n}"' for n in ordered[i : i + 6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
