#!/usr/bin/env python3
"""The console is the whole of the authentication in front of a tenant's vulnerability map.

    charts/tests/cva_console.py

Confidential Vulnerability Analysis publishes one hostname. Behind it sits an analysis API with
no authentication of its own — upstream's `nat serve` publishes the pipeline, the report
endpoints and a Swagger UI unauthenticated unless an OAuth2 issuer is configured — plus every
report it has ever written, plus a CONNECT proxy. These are the properties of that arrangement a
render can be held to:

1. **One Ingress, one backend, and it is the console.** A second path or a second backend on that
   hostname publishes something that was never meant to be reachable.
2. **The analysis API and the egress guard listen on loopback, have no `ports:` and no Service.**
   This is the property that makes the arrangement safe rather than merely conventional: the
   console's session check is the only thing in front of the API *because* nothing else can
   reach it. A Service for either, or a listen address that is not 127.0.0.1, hands an
   unauthenticated analysis API and an open proxy to every other pod in the cluster space.
3. **Every credential reaches a container by `secretKeyRef`.** An env *value* is rendered into
   the manifests, travels in the bundle in clear and is published in the evidence snapshot.
4. **The probes are on routes that answer without a session**, and the api's and the guard's are
   `exec` — a kubelet httpGet probe connects to the POD's address, which neither process listens
   on, so an httpGet probe there fails every time while the service answers the console
   perfectly.

What a render cannot check is whether the session logic is right. That was exercised against the
built image before this listing was published: `/healthz` answers 200 without a session,
`/api/status` answers 401, a wrong password answers 401, a forged cookie answers 401, and a
correct password sets `HttpOnly; Secure; SameSite=lax`.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

CHART = "charts/confidential-vulnerability-analysis"
VALUES = "charts/tests/cases/vuln-analysis-stack.yaml"
CREDENTIALS = {"console-password", "console-session-key", "inference-api-key"}
# The console's probes may only use a route that answers without a session.
CONSOLE_PROBE_PATHS = {"/healthz"}
# Loopback, and nothing else. Both appear in the api container's own argument list and in the
# guard's environment, so the check reads the render rather than the values file.
LOOPBACK = "127.0.0.1"


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "va", CHART, "--namespace", "vuln-analysis",
         "--skip-tests", "--values", VALUES],
        capture_output=True, text=True, check=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def containers(doc: dict):
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    return list(spec.get("initContainers", [])) + list(spec.get("containers", []))


def main() -> int:
    docs = render()
    problems: list[str] = []

    ingresses = [doc for doc in docs if doc["kind"] == "Ingress"]
    if len(ingresses) != 1:
        problems.append(f"{len(ingresses)} Ingress objects; this chart publishes one hostname")
    else:
        rules = ingresses[0]["spec"]["rules"]
        if len(rules) != 1:
            problems.append(f"the Ingress carries {len(rules)} rules")
        else:
            backends = {p["backend"]["service"]["name"] for p in rules[0]["http"]["paths"]}
            if backends != {"confidential-vuln-analysis-console"}:
                problems.append(
                    f"the hostname routes to {sorted(backends)} — everything on it must reach "
                    "the console, which is the only thing that checks a session"
                )
        if not ingresses[0]["spec"].get("ingressClassName"):
            problems.append(
                "the Ingress has no ingressClassName: it is created, its hostname is "
                "registered, DNS is written for it, and nothing serves it"
            )

    services = {doc["metadata"]["name"] for doc in docs if doc["kind"] == "Service"}
    if services != {"confidential-vuln-analysis-console"}:
        problems.append(
            f"this chart renders {sorted(services)}; the console's Service is the only one it "
            "may render — a Service for the analysis API or the egress guard publishes an "
            "unauthenticated API and an open proxy to every pod in the cluster space"
        )

    secret_names = {doc["metadata"]["name"] for doc in docs if doc["kind"] == "Secret"}

    for doc in docs:
        for container in containers(doc):
            where = f'{doc["kind"]}/{doc["metadata"]["name"]}:{container["name"]}'
            arguments = [str(a) for a in container.get("args", []) + container.get("command", [])]

            if container["name"] == "api":
                if not any(f"--host={LOOPBACK}" == a for a in arguments):
                    problems.append(
                        f"{where} is not started with --host={LOOPBACK}: the analysis API would "
                        "listen on the pod's address, where every other pod in the space can "
                        "reach it without a session"
                    )
                if container.get("ports"):
                    problems.append(f"{where} declares ports; a loopback listener has none")
            if container["name"] == "egress":
                listen = {e["name"]: e.get("value") for e in container.get("env", [])}
                if listen.get("EGRESS_LISTEN_ADDRESS") != LOOPBACK:
                    problems.append(
                        f"{where} listens on {listen.get('EGRESS_LISTEN_ADDRESS')}: an open "
                        "proxy on a pod address is an egress path for the whole cluster space"
                    )
                if container.get("ports"):
                    problems.append(f"{where} declares ports; a loopback listener has none")

            for env in container.get("env", []):
                if env.get("value") is None:
                    ref = (env.get("valueFrom") or {}).get("secretKeyRef") or {}
                    if ref.get("key") in CREDENTIALS and ref.get("name") not in secret_names:
                        problems.append(f"{where} {env['name']} points at a Secret this chart "
                                        f"does not render ({ref.get('name')})")
                    continue
                if "FOR-THE-GOLDEN" in str(env["value"]):
                    problems.append(
                        f"{where} carries {env['name']} as an env value, which the evidence "
                        "snapshot publishes in clear"
                    )
            for argument in arguments:
                if "FOR-THE-GOLDEN" in argument:
                    problems.append(f"{where} carries a credential in its command line")

        if doc["kind"] != "Deployment":
            continue
        for container in containers(doc):
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                configured = container.get(probe) or {}
                if not configured:
                    continue
                http = configured.get("httpGet")
                if container["name"] in ("api", "egress"):
                    if http:
                        problems.append(
                            f'{container["name"]}\'s {probe} is an httpGet: the kubelet connects '
                            "to the pod's address, which this process does not listen on, so the "
                            "probe fails every time while the service answers"
                        )
                    continue
                if container["name"] == "console" and http and \
                        http.get("path") not in CONSOLE_PROBE_PATHS:
                    problems.append(
                        f'the console\'s {probe} is on {http.get("path")}, which is behind the '
                        "session — the pod would never become ready"
                    )

    if problems:
        print("  FAIL  the console is not the only door")
        for problem in problems:
            print(f"          {problem}")
        return 1

    print("  ok    one Ingress to the console, one Service, the api and the guard on loopback "
          f"with exec probes, and {len(CREDENTIALS)} credentials by reference only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
