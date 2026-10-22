#!/usr/bin/env python3
"""What `api_routes.py` can read out of a controller, tested rather than assumed.

`ROUTER_SRC=... api_routes.py` is the only thing that notices a controller
route ROUTES does not name, and its one unacceptable failure is the silent one:
a route it does not see is reported as `all N controller routes in ROUTER_SRC
are named`. `@Post([CONSOLE_EVENTS_PATH, LEGACY_CONSOLE_EVENTS_PATH])` was that
miss — the console ingest moved to `/v1/console-events` in SUP-463 and no
check said so (SUP-482).

So each decorator shape is exercised here against fixture controllers, and each
case asserts one of two outcomes: the routes it declares, or a failure naming
the file and line. Never nothing.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

from api_routes import CONTROLLERS, declared_paths, source_routes, string_constants

SRC = "apps/router-api/src"

# (what the shape is, files to write under the checkout, routes expected,
#  a fragment every unreadable decorator's report must contain).
CASES = [
    (
        "a prefix and an empty argument",
        {f"{SRC}/health.controller.ts": "@Controller('health')\nclass H {\n  @Get()\n  read() {}\n}\n"},
        {("GET", "/health")},
        [],
    ),
    (
        "inline paths, single and double quoted",
        {f"{SRC}/data.controller.ts":
            "@Controller('admin/data')\nclass D {\n  @Get('export')\n  out() {}\n  @Post(\"import\")\n  in_() {}\n}\n"},
        {("GET", "/admin/data/export"), ("POST", "/admin/data/import")},
        [],
    ),
    (
        "an array of inline paths",
        {f"{SRC}/v1.controller.ts": "@Controller('v1')\nclass V {\n  @Get(['models', 'models/*id'])\n  list() {}\n}\n"},
        {("GET", "/v1/models"), ("GET", "/v1/models/*id")},
        [],
    ),
    (
        # SUP-482: the shape the scan used to drop on the floor.
        "an array of constants from the controller's own file",
        {f"{SRC}/ingest.controller.ts":
            "export const CONSOLE_EVENTS_PATH = 'v1/console-events';\n"
            "export const LEGACY_CONSOLE_EVENTS_PATH = 'v1/analytics/events';\n"
            "@Controller()\nclass I {\n  @Post([CONSOLE_EVENTS_PATH, LEGACY_CONSOLE_EVENTS_PATH])\n  take() {}\n}\n"},
        {("POST", "/v1/console-events"), ("POST", "/v1/analytics/events")},
        [],
    ),
    (
        "a constant declared in a sibling file",
        {f"{SRC}/paths.ts": "export const EVENTS_PATH: string = 'v1/console-events';\n",
         f"{SRC}/ingest.controller.ts": "@Controller()\nclass I {\n  @Post(EVENTS_PATH)\n  take() {}\n}\n"},
        {("POST", "/v1/console-events")},
        [],
    ),
    (
        "a prefix that is itself an array",
        {f"{SRC}/both.controller.ts": "@Controller(['v1', 'v2'])\nclass B {\n  @Get('models')\n  list() {}\n}\n"},
        {("GET", "/v1/models"), ("GET", "/v2/models")},
        [],
    ),
    (
        "a template literal, which the scan does not evaluate",
        {f"{SRC}/computed.controller.ts":
            "@Controller('v1')\nclass C {\n  @Post(`events/${version}`)\n  take() {}\n}\n"},
        set(),
        ["computed.controller.ts:3", "cannot read"],
    ),
    (
        "a concatenation",
        {f"{SRC}/joined.controller.ts":
            "const BASE = 'v1';\n@Controller('v1')\nclass J {\n  @Get(BASE + '/models')\n  list() {}\n}\n"},
        set(),
        ["joined.controller.ts:4", "cannot read"],
    ),
    (
        "an argument carrying a nested call",
        {f"{SRC}/called.controller.ts":
            "@Controller('v1')\nclass K {\n  @Get(pathFor('models'))\n  list() {}\n}\n"},
        set(),
        ["called.controller.ts:3", "cannot read"],
    ),
    (
        "a name nothing in the checkout declares",
        {f"{SRC}/absent.controller.ts": "@Controller('v1')\nclass A {\n  @Post(MISSING_PATH)\n  take() {}\n}\n"},
        set(),
        ["absent.controller.ts:3", "cannot read"],
    ),
    (
        "a name two files define differently, which the scan will not pick between",
        {f"{SRC}/one/paths.ts": "export const EVENTS_PATH = 'v1/console-events';\n",
         f"{SRC}/two/paths.ts": "export const EVENTS_PATH = 'v1/analytics/events';\n",
         f"{SRC}/ambiguous.controller.ts": "@Controller()\nclass A {\n  @Post(EVENTS_PATH)\n  take() {}\n}\n"},
        set(),
        ["ambiguous.controller.ts:3", "cannot read"],
    ),
    (
        "a route under a prefix the scan could not read, which is not guessed at",
        {f"{SRC}/prefixed.controller.ts":
            "@Controller(prefixFor('v1'))\nclass P {\n  @Get('models')\n  list() {}\n}\n"},
        set(),
        ["prefixed.controller.ts:1", "prefixed.controller.ts:3"],
    ),
    (
        # `*.controller.ts` is this repository's expectation, not Nest's
        # requirement, so a controller under another name is reported rather
        # than passed over: that is SUP-275 one filename away.
        "a controller in a file the scan does not read for routes",
        {f"{SRC}/routes.ts": "@Controller('admin/data')\nclass D {\n  @Get('export')\n  out() {}\n}\n"},
        set(),
        ["routes.ts:1", f"routes are read from {CONTROLLERS} only"],
    ),
    (
        "a controller declared by a test, which no deployment mounts",
        {f"{SRC}/fixture.spec.ts": "@Controller('fixture')\nclass F {\n  @Get()\n  read() {}\n}\n"},
        set(),
        [],
    ),
    (
        "a function-local constant, which is not a path a decorator can name",
        {f"{SRC}/local.controller.ts":
            "function build() {\n  const INNER_PATH = 'v1/inner';\n  return INNER_PATH;\n}\n"
            "@Controller()\nclass L {\n  @Post(INNER_PATH)\n  take() {}\n}\n"},
        set(),
        ["local.controller.ts:7", "cannot read"],
    ),
]

# (decorator argument, the paths it declares or None) — the parser on its own,
# where a case needs no checkout to state.
ARGUMENTS = [
    ("", [""]),
    ("'v1/models'", ["v1/models"]),
    ('"v1/models"', ["v1/models"]),
    ("\n  'export',\n", ["export"]),
    ("['a', 'b']", ["a", "b"]),
    ("[\n  'a',\n  'b',\n]", ["a", "b"]),
    ("[]", None),
    ("`v1/${x}`", None),
    ("{ path: 'v1' }", None),
    ("NAMED", ["named/path"]),
    ("[NAMED, 'b']", ["named/path", "b"]),
    ("[NAMED, OTHER]", None),
]


def checkout(files: dict[str, str], root: pathlib.Path) -> pathlib.Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def main() -> int:
    failures = []

    for argument, expected in ARGUMENTS:
        got = declared_paths(argument, {"NAMED": "named/path", "OTHER": None})
        if got != expected:
            failures.append(f"@Get({argument!r}) read as {got}, expected {expected}")

    for what, files, routes, reported in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            declared, unreadable = source_routes(checkout(files, pathlib.Path(tmp)))
        if declared != routes:
            failures.append(f"{what}: declared {sorted(declared)}, expected {sorted(routes)}")
        if reported and not unreadable:
            failures.append(f"{what}: read nothing and reported nothing — the route vanished")
        if not reported and unreadable:
            failures.append(f"{what}: reported {unreadable}, expected no failure")
        for fragment in reported:
            if not any(fragment in message for message in unreadable):
                failures.append(f"{what}: no report mentions {fragment!r}; got {unreadable}")

    # A constant the scan cannot pin to one value is None rather than a guess.
    # Neither a fixture in a test nor a name inside a function is what makes it
    # ambiguous — "module-level" is the claim the docstring makes, so it is the
    # claim under test.
    with tempfile.TemporaryDirectory() as tmp:
        root = checkout({f"{SRC}/a.ts": "export const P = 'one';\n",
                         f"{SRC}/b.ts": "const P = 'two';\n",
                         f"{SRC}/c.ts": "export const Q = 'three';\n",
                         f"{SRC}/c.spec.ts": "const Q = 'a fixture';\n",
                         f"{SRC}/d.ts": "function f() {\n  const Q = 'a local';\n}\n"}, pathlib.Path(tmp))
        constants = string_constants(root / SRC)
    if constants.get("P") is not None or constants.get("Q") != "three":
        failures.append(f"string_constants read {constants}, expected P=None and Q='three'")

    for message in failures:
        print(f"  FAIL  {message}")
    if failures:
        return 1
    print(f"{len(CASES)} decorator shapes read or reported, {len(ARGUMENTS)} arguments parsed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
