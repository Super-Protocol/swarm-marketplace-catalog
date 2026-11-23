#!/usr/bin/env python3
"""
Keycloak's JVM runs with the Base64 intrinsic off, in the LISTING and not only in the chart.

CVE-2026-114938 ("Java4Shell") is an out-of-bounds write in the HotSpot x86-64 Base64
intrinsic on AVX-512 VBMI processors, demonstrated as a pre-auth remote code execution
against Keycloak in its default configuration. No fixed JDK build exists — the pinned
image carries OpenJDK 21.0.6 and the current Keycloak release ships the same one — so
the only mitigation available is to turn the intrinsic off for the JVM that serves
sign-in. `conversational-analyst-identity` does that with
`_JAVA_OPTIONS=-XX:-UseBASE64Intrinsics`.

Why this needs a check of its own rather than a golden. The chart carries the flag in
`keycloak.extraEnvVars`, and a definition that sets that key does not append to the
chart's list — it REPLACES it. The listing sets it, for four other variables. So the
chart's own default is dead for every marketplace deployment, and the flag survives only
because the listing repeats it. Dropping one line from a list of five turns the
mitigation off in a deployment that renders, deploys, passes every golden and serves
sign-in exactly as before.

Two things are therefore asserted, and the first is the one that matters:

  * the LISTING's identity component sets `_JAVA_OPTIONS` and its value turns the
    intrinsic off;
  * rendering the chart with the listing's own list puts it in the Keycloak container's
    env, and nothing else in that container defines `_JAVA_OPTIONS` — a duplicate would
    make which value wins undefined, which is the trap upstream's chart guards against
    for `JAVA_OPTS_APPEND`.

`JAVA_OPTS_APPEND` is deliberately not the variable used: the vendored bitnami chart
writes it into its env ConfigMap with the jgroups DNS query whenever `cache.enabled` is
true, which it is.

    charts/tests/keycloak_jvm.py conversational-analyst charts/tests/cases/analyst-identity.yaml
"""

import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

VARIABLE = "_JAVA_OPTIONS"
FLAG = "-XX:-UseBASE64Intrinsics"
COMPONENT = "identity"
CHART = "conversational-analyst-identity"


def listing_env(app: str) -> list[dict]:
    definition = yaml.safe_load((ROOT / "apps" / app / "app.yaml").read_text())
    for component in definition["components"]:
        if component["name"] == COMPONENT:
            base = component["deployment"]["values"]["base"]
            return base["keycloak"]["extraEnvVars"]
    raise SystemExit(f"{app}: no component named {COMPONENT!r}")


def keycloak_container(documents: list[dict]) -> dict:
    for document in documents:
        if not document or document.get("kind") != "StatefulSet":
            continue
        containers = document["spec"]["template"]["spec"]["containers"]
        for container in containers:
            if "keycloak" in container["name"]:
                return container
    raise SystemExit("no Keycloak container in the render")


def main(app: str, case: str) -> int:
    failures = []

    env = listing_env(app)
    declared = [entry for entry in env if entry.get("name") == VARIABLE]
    if not declared:
        failures.append(
            f"apps/{app}/app.yaml: the {COMPONENT} component's keycloak.extraEnvVars does not set "
            f"{VARIABLE}. A definition's list replaces the chart's, so the chart's own default is "
            f"not in effect here — {FLAG} has to be in this list (CVE-2026-114938)."
        )
    elif len(declared) > 1:
        failures.append(f"apps/{app}/app.yaml: {VARIABLE} is declared {len(declared)} times.")
    elif FLAG not in str(declared[0].get("value", "")):
        failures.append(
            f"apps/{app}/app.yaml: {VARIABLE} is {declared[0].get('value')!r}, which does not "
            f"carry {FLAG} (CVE-2026-114938)."
        )
    else:
        print(f"  ok    apps/{app}/app.yaml: the {COMPONENT} component sets {VARIABLE}={FLAG}")

    rendered = subprocess.run(
        [
            "helm", "template", "analyst", str(ROOT / "charts" / CHART),
            "-f", str(ROOT / case),
            "--set-json", f"keycloak.extraEnvVars={json.dumps(env)}",
        ],
        capture_output=True,
        text=True,
    )
    if rendered.returncode != 0:
        print(rendered.stderr.strip())
        failures.append(f"helm template {CHART} failed with the listing's own env list")
    else:
        container = keycloak_container(list(yaml.safe_load_all(rendered.stdout)))
        present = [e for e in container.get("env", []) if e.get("name") == VARIABLE]
        if len(present) != 1:
            failures.append(
                f"{CHART}: the Keycloak container defines {VARIABLE} {len(present)} time(s); "
                "exactly one is required — a duplicate leaves which value wins undefined."
            )
        elif FLAG not in str(present[0].get("value", "")):
            failures.append(f"{CHART}: the rendered {VARIABLE} does not carry {FLAG}")
        else:
            print(f"  ok    {CHART}: the Keycloak container's {VARIABLE} carries {FLAG}, once")

    for failure in failures:
        print(f"  FAIL  {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
