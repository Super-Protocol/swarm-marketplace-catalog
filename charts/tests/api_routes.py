#!/usr/bin/env python3
"""
Every route the router API serves is either published by the chart's Ingress or
kept off it on purpose — and this file says which.

A controller is tested in-process and against a local container, where every
path answers. A marketplace deployment is reached through the chart's Ingress,
which routes a list of prefixes and answers 404 itself for anything else. So a
controller mounted under a prefix nobody added to `ingress.paths` passes every
test the router has and is unreachable on every deployment: the data
export/import (`/admin/data`) and the invitation codes CSV
(`/admin/invite-codes`) shipped that way in chart 0.11.0, with the screens and
the buttons in place and nothing behind them (SUP-275).

No golden can say that — the Ingress rendered exactly what the values asked
for. What was missing is the other side of the comparison, so it is written
down here: ROUTES is every route the API mounts at the build the chart pins,
and whether a client outside the cluster calls it. The check renders the chart
as the listing deploys it and requires that

  - every published route is matched by an Ingress path, the way ingress-nginx
    matches a `Prefix` path — element by element, so `/admin` carries
    `/admin/data/export` and does not carry `/administrator`;
  - no internal route is;
  - every Ingress path carries at least one route, so the list stays a decision
    rather than a leftover;
  - the listing does not replace the list, because then the chart's is not the
    one that deploys.

The router's source is not in this repository, so ROUTES is a copy and a copy
drifts. Bumping the API image is the moment it does: run this against a
checkout of the build being pinned, and a controller route ROUTES does not
name fails the check instead of shipping unrouted —

    charts/tests/api_routes.py
    ROUTER_SRC=../confidential-router charts/tests/api_routes.py

That scan reads the source as text, so what it can read is a limit worth
writing down. It understands a route decorator whose argument is empty, one
inline string, or an array of them, and a `const NAME = 'path'` a decorator
names instead of spelling out:

    @Controller()  @Get('export')  @Post([CONSOLE_EVENTS_PATH, 'v1/legacy'])

It does not evaluate anything else — a template literal, a concatenation, a
computed path, a name two files define differently, an argument carrying a
nested call. None of those is skipped: every `@Controller`/`@Get`/`@Post`/… it
finds and cannot read is a failure naming the file and line, because a route
this file does not see reads as `ok`, and that is the one answer it must never
give wrongly. `@Post([A, B])` was exactly that miss until SUP-482: the console
ingest moved to `/v1/console-events` in SUP-463 and the scan kept reporting
every route named.
"""

import os
import pathlib
import re
import subprocess
import sys

import yaml

CHART = "confidential-router-api"
# What the listing renders with its form left as it arrives.
CASE = "api-external-only"
LISTING = "apps/confidential-router/app.yaml"

PUBLISHED, INTERNAL = "published", "internal"

# (method, route, audience, who calls it) — router-api at `main-e670363`, the
# build listing 0.19.4 pins.
# `:name` and `*name` are Nest's parameter and wildcard segments.
ROUTES = [
    # Not controllers: mounted in `bootstrap.ts` and by the GraphQL module.
    ("ALL", "/auth/*path", PUBLISHED, "Better Auth and the emailed-code sign-in, from the browser"),
    ("POST", "/graphql", PUBLISHED, "the console's data layer"),
    ("GET", "/docs", INTERNAL, "Swagger"),
    # Controllers.
    ("GET", "/health", INTERNAL, "the kubelet's probes"),
    ("POST", "/v1/chat/completions", PUBLISHED, "API clients"),
    ("POST", "/v1/completions", PUBLISHED, "API clients"),
    ("POST", "/v1/embeddings", PUBLISHED, "API clients"),
    ("ALL", "/v1/*path", PUBLISHED, "API clients — the OpenAI-shaped 404"),
    ("GET", "/v1/models", PUBLISHED, "API clients"),
    ("GET", "/v1/models/*id", PUBLISHED, "API clients"),
    ("GET", "/v1/generation", PUBLISHED, "API clients"),
    ("GET", "/v1/evidence", PUBLISHED, "anyone verifying the deployment"),
    ("GET", "/v1/evidence/:endpoint", PUBLISHED, "anyone verifying the deployment"),
    ("GET", "/v1/invites/:code", PUBLISHED, "the landing page"),
    ("POST", "/v1/console-events", PUBLISHED, "the console and the landing page (SUP-463)"),
    # The path the ingest answered before SUP-463, kept by the pinned build for
    # one release so a console served from a cache still reaches it. It goes
    # when the build that stops serving it is pinned.
    ("POST", "/v1/analytics/events", PUBLISHED, "the console and the landing page — superseded by /v1/console-events"),
    ("POST", "/v1/webhooks/typeform", PUBLISHED, "Typeform"),
    ("POST", "/billing/stripe/webhook", PUBLISHED, "Stripe"),
    ("GET", "/billing/manual/complete", PUBLISHED, "the manual provider's checkout return link"),
    ("GET", "/activity/generations.csv", PUBLISHED, "the Logs screen's CSV download"),
    ("GET", "/exports/evidence.zip", PUBLISHED, "a signed link that may be handed on"),
    ("GET", "/admin/data/export", PUBLISHED, "the console's Export & import screen (SUP-271)"),
    ("POST", "/admin/data/import", PUBLISHED, "the console's Export & import screen (SUP-271)"),
    ("GET", "/admin/invite-codes/export.csv", PUBLISHED, "the Codes tab's Export CSV (SUP-272)"),
    ("POST", "/admin/invite-codes/import", PUBLISHED, "the Codes tab's Import CSV (SUP-272)"),
]


def elements(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def concrete(route: str) -> str:
    """A request path the route answers: each parameter segment filled in."""
    return "/" + "/".join(part.lstrip(":*") if part[:1] in ":*" else part for part in elements(route))


def carries(prefix: str, path: str) -> bool:
    """Whether a `pathType: Prefix` Ingress path matches a request path."""
    want, have = elements(prefix), elements(path)
    return have[: len(want)] == want


def ingress_paths() -> list[str]:
    output = subprocess.run(
        ["helm", "template", "cr", f"charts/{CHART}", "--namespace", "confidential-router",
         "--values", f"charts/tests/cases/{CASE}.yaml"],
        capture_output=True, text=True, check=True,
    ).stdout
    ingresses = [d for d in yaml.safe_load_all(output)
                 if d and d["kind"] == "Ingress" and d["metadata"]["name"] == CHART]
    if len(ingresses) != 1:
        sys.exit(f"  FAIL  {CHART} renders {len(ingresses)} Ingress/{CHART}, expected one")
    paths = []
    for rule in ingresses[0]["spec"].get("rules") or []:
        for entry in (rule.get("http") or {}).get("paths") or []:
            if entry.get("pathType") != "Prefix":
                sys.exit(f"  FAIL  {entry['path']} is pathType {entry.get('pathType')}; this check reads Prefix")
            paths.append(entry["path"])
    return paths


def listing_overrides() -> list[str]:
    """Where the listing sets `ingress.paths` itself, if anywhere."""
    with open(LISTING, encoding="utf-8") as handle:
        definition = yaml.safe_load(handle)
    found = []
    for component in definition["components"]:
        if component["source"].get("chart") != CHART:
            continue
        values = (component.get("deployment") or {}).get("values") or {}
        if "paths" in ((values.get("base") or {}).get("ingress") or {}):
            found.append("values.base.ingress.paths")
        for patch in values.get("patches") or []:
            if patch.get("path", "").startswith("/ingress/paths"):
                found.append(f"a patch on {patch['path']}")
    return found


# Every route decorator, with its argument captured whole rather than matched.
# `[^)]*` stops at the first `)`, so an argument this scan does not expect — a
# nested call, anything multi-line that closes elsewhere — arrives here
# unparsed and is reported, instead of the decorator matching nothing and the
# route disappearing.
DECORATOR = re.compile(r"@(Controller|Get|Post|Put|Patch|Delete|All|Head|Options|Search)\(([^)]*)\)")
# A path a decorator names by identifier: `export const CONSOLE_EVENTS_PATH = 'v1/console-events'`.
CONSTANT = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(['\"])([^'\"\n]*)\2\s*(?:as\s+const\s*)?;?\s*$",
    re.MULTILINE,
)
STRING = re.compile(r"^(['\"])([^'\"]*)\1$")


def string_constants(source: pathlib.Path) -> dict[str, str | None]:
    """Module-level string constants in a router checkout, by name.

    A name two files define differently maps to None: which one a decorator
    meant is not a question a text scan may answer by picking. Tests are left
    out, because a fixture is free to reuse a name a controller's path happens
    to have and nothing is routed by one.
    """
    values: dict[str, str | None] = {}
    for file in sorted(path for path in source.rglob("*.ts") if not path.name.endswith(".spec.ts")):
        for name, _, value in CONSTANT.findall(file.read_text(encoding="utf-8")):
            if name in values and values[name] != value:
                values[name] = None
            else:
                values.setdefault(name, value)
    return values


def declared_paths(argument: str, constants: dict[str, str | None]) -> list[str] | None:
    """The paths one route decorator declares, or None if it cannot be read.

    Nest takes a single path or an array of them, written inline or named by a
    constant: `()`, `('v1/models')`, `([CONSOLE_EVENTS_PATH, 'v1/legacy'])`.
    """
    text = argument.strip().rstrip(",").strip()
    if not text:
        return [""]
    if text.startswith("[") and text.endswith("]"):
        items = [item.strip() for item in text[1:-1].split(",")]
    else:
        items = [text]
    paths = []
    for item in items:
        if not item:
            continue
        literal = STRING.match(item)
        value = literal.group(2) if literal else constants.get(item)
        if value is None:
            return None
        paths.append(value)
    return paths or None


def source_routes(root: pathlib.Path) -> tuple[set[tuple[str, str]], list[str]]:
    """Every (method, route) a controller in a router checkout declares, and
    every route decorator in it this scan could not read."""
    routes, unreadable = set(), []
    source = root / "apps" / "router-api" / "src"
    if not source.is_dir():
        sys.exit(f"  FAIL  ROUTER_SRC={root} has no apps/router-api/src")
    constants = string_constants(source)
    for file in sorted(source.rglob("*.controller.ts")):
        where, prefixes = file.relative_to(root), None
        text = file.read_text(encoding="utf-8")
        for match in DECORATOR.finditer(text):
            kind, argument = match.group(1), match.group(2)
            line = text.count("\n", 0, match.start()) + 1
            paths = declared_paths(argument, constants)
            if paths is None:
                unreadable.append(
                    f"{where}:{line} declares @{kind}({' '.join(argument.split())}),"
                    " a route this scan cannot read"
                )
                if kind == "Controller":
                    prefixes = None
            elif kind == "Controller":
                prefixes = paths
            elif prefixes is None:
                unreadable.append(f"{where}:{line} declares @{kind} under no prefix this scan could read")
            else:
                routes.update(
                    (kind.upper(), "/" + "/".join(elements(f"{prefix}/{path}")))
                    for prefix in prefixes
                    for path in paths
                )
    return routes, unreadable


def main() -> int:
    failures = []
    paths = ingress_paths()

    for method, route, audience, caller in ROUTES:
        matched = [prefix for prefix in paths if carries(prefix, concrete(route))]
        if audience == PUBLISHED and not matched:
            failures.append(f"{method} {route} — {caller} — is not routed: the Ingress answers 404 for it")
        if audience == INTERNAL and matched:
            failures.append(f"{method} {route} — {caller} — is published by {matched[0]}")

    for prefix in paths:
        if not any(carries(prefix, concrete(route)) for _, route, _, _ in ROUTES):
            failures.append(f"the Ingress routes {prefix}, which no route in ROUTES is under")

    for where in listing_overrides():
        failures.append(f"{LISTING} sets the list itself ({where}), so the chart's is not what deploys")

    scanned = ""
    if os.environ.get("ROUTER_SRC"):
        declared, unreadable = source_routes(pathlib.Path(os.environ["ROUTER_SRC"]))
        failures.extend(unreadable)
        known = {(method, route) for method, route, _, _ in ROUTES}
        for method, route in sorted(declared - known):
            failures.append(f"{method} {route} is a controller route ROUTES does not name — published or internal?")
        scanned = f"; all {len(declared)} controller routes in ROUTER_SRC are named"

    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1

    published = sum(1 for _, _, audience, _ in ROUTES if audience == PUBLISHED)
    print(f"  ok    {published} published routes reach the API through {', '.join(paths)}")
    print(f"  ok    {len(ROUTES) - published} internal routes stay off the Ingress{scanned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
