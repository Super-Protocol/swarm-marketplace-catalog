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

The second half of this file does what a render cannot, for the three checks the console performs
on what a signed-in caller sends it (SUP-891). `console.py` is loaded **out of the rendered
`…-app` ConfigMap** rather than out of `files/`, because the ConfigMap is what a deployment runs
and what the cloud signs, and it is driven as an ASGI application with no test client and no
network — the analysis API it relays to is a closed port, so a request that reaches the relay
answers `502` and one the validator refuses answers `404`, which is how the two are told apart:

1. **A report path part is a name, not a path.** `..` satisfies the character class the parts are
   matched against, and what refuses a traversal today is `_resolve_report_file` in the analysis
   API behind the relay — so the console's own check was defence-in-depth that was not there.
2. **A malformed `Content-Length` is a 400.** `int(declared)` raised out of the handler, which is
   a 500 and a traceback for what is a client's malformed request. uvicorn refuses such a header
   with a 400 of its own before the application is called — measured, under h11 and httptools
   both — which is why this is driven as ASGI rather than through a server: what is under test is
   the handler not depending on the layer in front of it.
3. **A GHSA identifier is refused with the reason.** `osv_lookup` is the only research tool this
   deployment has and it takes a CVE id, so a GHSA would be reported on with no source behind it.
   The refusal has to name why, and it has to happen before the pipeline is called at all.

Needs `fastapi` and `httpx`, which are what the console imports; without them this half prints a
note and the render half still runs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

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


# ---------------------------------------------------------------------------
# The console, out of the ConfigMap, driven as an ASGI application
# ---------------------------------------------------------------------------
APP_CONFIGMAP = "confidential-vuln-analysis-app"
PASSWORD = "FOR-THE-GOLDEN-console-password"
SESSION_KEY = "FOR-THE-GOLDEN-session-key"


def closed_port() -> int:
    """A port nothing listens on, so a request that reaches the relay fails to connect."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def load_console(docs: list[dict], data_root: Path):
    """Import the console as the pod runs it: the ConfigMap's code, its own environment."""
    sources = [
        doc["data"] for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == APP_CONFIGMAP
    ]
    if len(sources) != 1 or "console.py" not in sources[0]:
        raise AssertionError(f"no console.py in ConfigMap/{APP_CONFIGMAP}")

    module_path = data_root / "console.py"
    module_path.write_text(sources[0]["console.py"], encoding="utf-8")
    os.environ.update({
        "CONSOLE_PASSWORD": PASSWORD,
        "CONSOLE_SESSION_KEY": SESSION_KEY,
        "ANALYSIS_API_URL": f"http://127.0.0.1:{closed_port()}",
        "EGRESS_GUARD_URL": "",
        "DATA_ROOT": str(data_root),
        "UI_DIR": str(data_root),
        "LOG_LEVEL": "CRITICAL",
    })
    spec = importlib.util.spec_from_file_location("cva_console_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def request(app, method: str, path: str, headers: dict[str, str], body: bytes = b""):
    """One HTTP request, as ASGI. No test client: the headers are the thing under test."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 40000),
        "server": ("console", 8080),
    }
    pending = [{"type": "http.request", "body": body, "more_body": False}]
    answered: list[dict] = []

    async def receive() -> dict:
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        answered.append(message)

    try:
        await app(scope, receive, send)
    except Exception as error:  # noqa: BLE001 — a handler that raises is a 500, as uvicorn does
        if not any(m["type"] == "http.response.start" for m in answered):
            return 500, f"unhandled {type(error).__name__}: {error}"
    status = next(m["status"] for m in answered if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in answered if m["type"] == "http.response.body")
    return status, payload.decode("utf-8", "replace")


def behaviour(docs: list[dict]) -> list[str]:
    """The three checks the console performs on what a signed-in caller sends it."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as workdir:
        data_root = Path(workdir)
        console = load_console(docs, data_root)
        signed_in = {"cookie": f"cva_session={console.issue()}"}
        drive = lambda *a, **k: asyncio.run(request(console.app, *a, **k))  # noqa: E731

        # An uploaded root filesystem for the job form to name, as the console writes one.
        rootfs = data_root / "rootfs" / "demo"
        rootfs.mkdir(parents=True)
        (rootfs / ".upload.json").write_text(json.dumps(
            {"uploadedAt": "", "bytes": 1, "entries": 1,
             "filesystemPath": str(rootfs), "imageConfigPath": ""}))

        # -- 1. the report relay ------------------------------------------------------------
        # The baseline: a well-formed path is relayed, and the relay cannot connect, so 502 is
        # what "the validator let this through" looks like. Any 404 below is the validator.
        status, _ = drive("GET", "/api/reports/vulnerability/md/CVE-2024-3094.md", signed_in)
        if status != 502:
            problems.append(
                f"a well-formed report path answered {status}, not the 502 of a relay with "
                "nowhere to relay to — the rest of this check cannot tell a refusal from a pass"
            )
        for traversal in ("..", "."):
            for path in (
                f"/api/reports/{traversal}/md/report.md",
                f"/api/reports/vulnerability/{traversal}/report.md",
                f"/api/reports/vulnerability/md/{traversal}",
            ):
                status, _ = drive("GET", path, signed_in)
                if status != 404:
                    problems.append(
                        f"GET {path} answered {status}: `{traversal}` satisfies the character "
                        "class the parts are matched against, and the console refuses a part "
                        "that is a traversal rather than depending on the API behind it"
                    )
        # The separator half of the check, which no route can deliver — Starlette's `str`
        # convertor does not match a `/` — and which is therefore read off the function itself.
        safe_segment = getattr(console, "safe_segment", None)
        if safe_segment is None:
            problems.append(
                "the console has no `safe_segment`: every value it interpolates into a path on "
                "the analysis API goes through one check, or there is no check"
            )
        else:
            if safe_segment("etc/passwd", console.REPORT_PART_PATTERN):
                problems.append("a report part carrying a separator is accepted")
            if safe_segment("etc\\passwd", console.REPORT_PART_PATTERN):
                problems.append("a report part carrying a backslash is accepted")
            if not safe_segment("CVE-2024-3094.md", console.REPORT_PART_PATTERN):
                problems.append("an ordinary report file name is refused")
            if safe_segment("..", console.JOB_ID_PATTERN):
                problems.append(
                    "a job id of `..` is accepted: it is relayed into the API's own path the "
                    "same way a report part is"
                )
        status, _ = drive("GET", "/api/reports/vulnerability/md/report.md", {})
        if status != 401:
            problems.append(f"the report relay answered {status} with no session, not 401")

        # -- 2. the declared length ---------------------------------------------------------
        for declared, expected in (("banana", 400), ("", 400), ("-1", 400), ("1 2", 400),
                                   (str(console.MAX_UPLOAD_BYTES + 1), 413)):
            status, payload = drive(
                "PUT", "/api/rootfs/upload", {**signed_in, "content-length": declared}, b"",
            )
            if status != expected:
                problems.append(
                    f"PUT with Content-Length {declared!r} answered {status}, not {expected} — "
                    "a client's claim about its own request is parsed, not trusted, and a "
                    "malformed one is its own 400 rather than a traceback and a 500"
                )
            if expected == 400 and "byte count" not in payload:
                problems.append(
                    f"the refusal of Content-Length {declared!r} does not say what is wrong "
                    f"with the header: {payload[:120]}"
                )
        if (data_root / "rootfs" / "upload").exists():
            problems.append("a refused upload left a directory behind")
        # Absent is not malformed: the body decides, and an empty one is refused on its own.
        status, payload = drive("PUT", "/api/rootfs/noheader", signed_in, b"")
        if status != 400 or "empty" not in payload:
            problems.append(
                f"a PUT with no Content-Length answered {status} / {payload[:120]} — the header "
                "is a courtesy, and the stream is what bounds the write"
            )

        # -- 3. the identifiers the pipeline can research ------------------------------------
        def submit(identifier: str):
            return drive(
                "POST", "/api/jobs", {**signed_in, "content-type": "application/json"},
                json.dumps({
                    "rootfs": "demo", "imageName": "ubuntu", "imageTag": "22.04",
                    "vulns": [{"id": identifier}],
                }).encode(),
            )

        status, payload = submit("CVE-2024-3094")
        if status != 502:
            problems.append(
                f"a CVE identifier answered {status}, not the 502 of a pipeline that is not "
                f"listening: {payload[:160]}"
            )
        status, payload = submit("GHSA-jfh8-c2jp-5v3q")
        if status != 400:
            problems.append(
                f"a GHSA identifier answered {status}: `osv_lookup` is the only research tool "
                "this deployment has and it takes a CVE id, so an accepted GHSA is a report "
                "with no source behind it"
            )
        elif "CVE" not in payload or "GHSA" not in payload:
            problems.append(
                f"the GHSA refusal does not name why this deployment cannot research one: "
                f"{payload[:200]}"
            )
        status, payload = submit("not-an-identifier")
        if status != 400 or "CVE identifier" not in payload:
            problems.append(f"a malformed identifier answered {status} / {payload[:120]}")

    return problems


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

    # What the console itself imports: fastapi (which brings Starlette), httpx for the two
    # relays, and python-multipart, which FastAPI requires of any route taking a `Form` — the
    # sign-in route does, so the module does not load without it. Both module names are looked
    # for because the package has been importable under each.
    missing = [name for name in ("fastapi", "httpx") if importlib.util.find_spec(name) is None]
    if all(importlib.util.find_spec(name) is None for name in ("python_multipart", "multipart")):
        missing.append("python-multipart")
    if missing:
        print(f"  note  {', '.join(missing)} is not installed, so what the console does with a "
              "report path, a declared length and a GHSA identifier is unchecked")
        return 0

    problems = behaviour(docs)
    if problems:
        print("  FAIL  the console accepts something it cannot answer for")
        for problem in problems:
            print(f"          {problem}")
        return 1

    print("  ok    a report part that is `.` or `..` is refused by the console itself, a "
          "malformed Content-Length is a 400, and a GHSA identifier is refused with the reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
