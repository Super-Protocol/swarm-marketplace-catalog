#!/usr/bin/env python3
"""The console is the whole of the authentication in front of a tenant's documents.

    charts/tests/rag_console.py

Confidential RAG publishes one hostname. Behind it sit a retriever with no authentication of
its own, a vector store holding the tenant's corpus, and three inference endpoints. The console
is what stands between them and whoever finds the name, so these are the properties of it that
a render can be held to:

1. **One Ingress, one backend, and it is the console.** A second path or a second backend on
   that hostname publishes something that was never meant to be reachable. The marketplace's
   own trap list has this as "never publish an unauthenticated interface": a retriever on a
   public hostname hands the corpus to whoever finds it.
2. **Nothing else renders an Ingress.** The pipeline, the indexer and the store are ClusterIP.
3. **The password and the session key reach the container by `secretKeyRef`.** An env *value*
   is rendered into the manifests, travels in the deployment bundle in clear and is published
   in the evidence snapshot; a `secretKeyRef` is a pointer to something the platform sealed.
   The same for every inference key and the vector store's password, on every container.
4. **The probe path is the one unauthenticated route.** `/healthz` is reachable from the
   public hostname without a session by construction, so it has to be the only thing the
   probes use — a probe on a path behind the session would make the pod unready for ever, and
   a probe on a path that reports the corpus would publish it.

What a render cannot check is whether the session logic is right. That was exercised against
the built image before this listing was published: a wrong password answers 401, a forged or
expired cookie answers 401, `/api/status` and `/api/ask` answer 401 without one, and a correct
password sets an `HttpOnly; Secure; SameSite=lax` cookie.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

CHART = "charts/confidential-rag"
VALUES = "charts/tests/cases/rag-stack.yaml"
# Every key in the chart's Secret that is a credential. A container may name these only
# through `secretKeyRef`.
CREDENTIALS = {
    "console-password",
    "console-session-key",
    "inference-api-key",
    "embeddings-api-key",
    "reranker-api-key",
    "vector-store-password",
}
PROBE_PATHS = {"/healthz"}


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "rag", CHART, "--namespace", "confidential-rag",
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
            paths = rules[0]["http"]["paths"]
            backends = {path["backend"]["service"]["name"] for path in paths}
            if backends != {"confidential-rag-console"}:
                problems.append(
                    f"the hostname routes to {sorted(backends)} — everything on it must reach "
                    "the console, which is the only thing that checks a session"
                )
        if not ingresses[0]["spec"].get("ingressClassName"):
            problems.append(
                "the Ingress has no ingressClassName: it is created, its hostname is "
                "registered, DNS is written for it, and nothing serves it"
            )

    secret_names = {doc["metadata"]["name"] for doc in docs if doc["kind"] == "Secret"}

    for doc in docs:
        for container in containers(doc):
            where = f'{doc["kind"]}/{doc["metadata"]["name"]}:{container["name"]}'
            for env in container.get("env", []):
                if env.get("value") is None:
                    ref = (env.get("valueFrom") or {}).get("secretKeyRef") or {}
                    if ref.get("key") in CREDENTIALS and ref.get("name") not in secret_names:
                        problems.append(f"{where} {env['name']} points at a Secret this chart "
                                        f"does not render ({ref.get('name')})")
                    continue
                value = str(env["value"])
                # A credential as an env *value* is the failure. The case files use
                # recognisable placeholders, so look for those.
                for marker in ("ACCESS-PASSWORD-FOR-THE-GOLDEN",
                               "SESSION-SIGNING-KEY-FOR-THE-GOLDEN",
                               "INFERENCE-KEY-FOR-THE-GOLDEN",
                               "RERANKER-KEY-FOR-THE-GOLDEN",
                               "EMBEDDINGS-KEY-FOR-THE-GOLDEN",
                               "VECTOR-STORE-PASSWORD-FOR-THE-GOLDEN"):
                    if marker in value:
                        problems.append(
                            f"{where} carries {env['name']} as an env value, which the "
                            "evidence snapshot publishes in clear"
                        )
            for argument in container.get("args", []) + container.get("command", []):
                if "FOR-THE-GOLDEN" in str(argument):
                    problems.append(f"{where} carries a credential in its command line")

        if doc["kind"] != "Deployment":
            continue
        for container in containers(doc):
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                http = (container.get(probe) or {}).get("httpGet")
                if not http:
                    continue
                if doc["metadata"]["name"] == "confidential-rag-console" and \
                        http.get("path") not in PROBE_PATHS:
                    problems.append(
                        f'the console\'s {probe} is on {http.get("path")}, which is behind the '
                        "session — the pod would never become ready"
                    )

    if problems:
        print("  FAIL  the console is not the only door")
        for problem in problems:
            print(f"          {problem}")
        return 1

    print(f"  ok    one Ingress to the console, {len(CREDENTIALS)} credentials by reference "
          "only, and the probes are on the one unauthenticated route")
    return 0


if __name__ == "__main__":
    sys.exit(main())
