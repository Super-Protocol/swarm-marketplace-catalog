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

# (method, route, audience, who calls it) — router-api at `main-44708f9`.
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
    ("POST", "/v1/analytics/events", PUBLISHED, "the console and the landing page"),
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


DECORATOR = re.compile(r"@(Controller|Get|Post|Put|Patch|Delete|All)\(\s*(?:'([^']*)')?\s*\)")


def source_routes(root: pathlib.Path) -> set[tuple[str, str]]:
    """Every (method, route) a controller in a router checkout declares."""
    routes = set()
    source = root / "apps" / "router-api" / "src"
    if not source.is_dir():
        sys.exit(f"  FAIL  ROUTER_SRC={root} has no apps/router-api/src")
    for file in sorted(source.rglob("*.controller.ts")):
        prefix = None
        for kind, argument in DECORATOR.findall(file.read_text(encoding="utf-8")):
            if kind == "Controller":
                prefix = argument
            elif prefix is not None:
                routes.add((kind.upper(), "/" + "/".join(elements(f"{prefix}/{argument}"))))
    return routes


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
        declared = source_routes(pathlib.Path(os.environ["ROUTER_SRC"]))
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
