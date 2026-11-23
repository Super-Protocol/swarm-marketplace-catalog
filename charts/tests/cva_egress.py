#!/usr/bin/env python3
"""The egress guard admits one host, and the pipeline's clients are actually pointed at it.

    charts/tests/cva_egress.py

Two halves, and the listing's egress claim needs both:

**The guard does what it says.** `files/egress_guard.py` is loaded and driven over real sockets:
CONNECT to the allowed host and port is tunnelled, the same host on another port is refused, a
different host is refused, a plain proxied request is refused (so there is no cleartext path
out), and a request for the allowed host with the name extended — `api.osv.dev.evil.test`, the
shape a suffix match would admit — is refused. A guard with no allowlist refuses to start.

**The clients go through it.** The guard is only an allowlist if the thing it guards cannot go
around it. So the render is checked too: the api container carries HTTP_PROXY and HTTPS_PROXY at
the guard's loopback address, and NO_PROXY carries the composed engine's host — which is the one
exclusion that must be there, because the engine is inside the cluster space and routing it
through the guard would have the guard refuse the one call the pipeline cannot work without. An
over-broad NO_PROXY is the other failure, and it is checked as well: a bare `*`, or a suffix that
would swallow the allowed host, puts the pipeline back on the open internet with the guard
running and admitting nothing.

The admit path is exercised against a local listener rather than `api.osv.dev`, so this check
needs no network of its own. That the real host answers through the guard was verified by hand
before the listing was published (`GET /v1/vulns/CVE-2024-5187` → 200).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import subprocess
import sys

import yaml

GUARD = "charts/confidential-vulnerability-analysis/files/egress_guard.py"
CHART = "charts/confidential-vulnerability-analysis"
VALUES = "charts/tests/cases/vuln-analysis-stack.yaml"
ENGINE_HOST = "swarm-model-server"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def load_guard(allow_host: str, allow_port: int, listen_port: int):
    """Import the guard with its environment set, as the container runs it."""
    os.environ.update({
        "EGRESS_ALLOW_HOST": allow_host,
        "EGRESS_ALLOW_PORT": str(allow_port),
        "EGRESS_LISTEN_ADDRESS": "127.0.0.1",
        "EGRESS_LISTEN_PORT": str(listen_port),
        "LOG_LEVEL": "CRITICAL",
    })
    spec = importlib.util.spec_from_file_location(f"guard_{listen_port}", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def request(port: int, line: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{line}\r\nHost: probe\r\n\r\n".encode())
    await writer.drain()
    answer = await asyncio.wait_for(reader.read(4096), 10)
    writer.close()
    return answer


async def exercise() -> list[str]:
    problems: list[str] = []

    # The "upstream" the guard is allowed to reach: a local listener that answers one line, so
    # the admit path is a real tunnel rather than a mock.
    upstream_port = free_port()

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(64)
        writer.write(b"UPSTREAM-ANSWERED")
        await writer.drain()
        writer.close()

    upstream_server = await asyncio.start_server(upstream, "127.0.0.1", upstream_port)
    listen_port = free_port()
    guard = load_guard("127.0.0.1", upstream_port, listen_port)
    guard_server = await asyncio.start_server(guard.handle, "127.0.0.1", listen_port)

    try:
        # 1. the allowed host and port, tunnelled
        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        writer.write(f"CONNECT 127.0.0.1:{upstream_port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        if b"200 Connection established" not in head:
            problems.append(f"the allowed host was not admitted: {head!r}")
        else:
            writer.write(b"PING\r\n\r\n")
            await writer.drain()
            body = await asyncio.wait_for(reader.read(64), 10)
            if b"UPSTREAM-ANSWERED" not in body:
                problems.append(f"the tunnel did not relay bytes: {body!r}")
        writer.close()

        # 2. the same host, another port
        answer = await request(listen_port, f"CONNECT 127.0.0.1:{upstream_port + 1} HTTP/1.1")
        if b"403" not in answer:
            problems.append(f"the allowed host on another port was not refused: {answer[:120]!r}")

        # 3. a different host
        answer = await request(listen_port, "CONNECT example.test:443 HTTP/1.1")
        if b"403" not in answer:
            problems.append(f"a different host was not refused: {answer[:120]!r}")

        # 4. a plain proxied request — there is no cleartext path out
        answer = await request(listen_port, "GET http://example.test/ HTTP/1.1")
        if b"403" not in answer:
            problems.append(f"a plain proxied request was not refused: {answer[:120]!r}")

        # 5. the shape a suffix match would admit
        answer = await request(listen_port, f"CONNECT 127.0.0.1.example.test:{upstream_port} HTTP/1.1")
        if b"403" not in answer:
            problems.append("a hostname extending the allowed one was admitted: the match is "
                            "not exact, which is how an allowlist stops being one")

        # 6. the status route, which is the only non-CONNECT request the guard answers
        answer = await request(listen_port, "GET /_guard/status HTTP/1.1")
        if b"200 OK" not in answer or b"allowHost" not in answer:
            problems.append(f"the status route did not answer: {answer[:120]!r}")
    finally:
        guard_server.close()
        upstream_server.close()

    # 7. no allowlist, no guard
    if guard.admitted("anything", "443"):
        problems.append("admitted() is True for a host that is not the allowlist")
    empty = load_guard("", 443, free_port())
    if empty.admitted("api.osv.dev", "443"):
        problems.append("a guard with an empty allowlist admits traffic")

    return problems


def check_render() -> list[str]:
    problems: list[str] = []
    out = subprocess.run(
        ["helm", "template", "va", CHART, "--namespace", "vuln-analysis",
         "--skip-tests", "--values", VALUES],
        capture_output=True, text=True, check=True,
    ).stdout
    docs = [doc for doc in yaml.safe_load_all(out) if doc]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    containers = {c["name"]: c for c in deployment["spec"]["template"]["spec"]["containers"]}

    if "egress" not in containers:
        return ["the egress guard is not rendered"]

    api_env = {e["name"]: e.get("value") for e in containers["api"].get("env", [])}
    guard_env = {e["name"]: e.get("value") for e in containers["egress"].get("env", [])}
    expected = f"http://{guard_env['EGRESS_LISTEN_ADDRESS']}:{guard_env['EGRESS_LISTEN_PORT']}"
    for name in ("HTTP_PROXY", "HTTPS_PROXY"):
        if api_env.get(name) != expected:
            problems.append(
                f"the api's {name} is {api_env.get(name)!r}, not the guard at {expected} — "
                "every client in that container would reach the internet directly"
            )

    no_proxy = [entry.strip() for entry in (api_env.get("NO_PROXY") or "").split(",") if entry.strip()]
    if ENGINE_HOST not in no_proxy:
        problems.append(
            f"NO_PROXY does not carry {ENGINE_HOST}: the call to the engine would go through "
            "the guard, which admits one external host and would refuse it — every model call "
            "in the deployment fails"
        )
    allow_host = guard_env["EGRESS_ALLOW_HOST"]
    for entry in no_proxy:
        if entry == "*" or allow_host == entry or allow_host.endswith(entry.lstrip("*")):
            problems.append(
                f"NO_PROXY carries {entry!r}, which excludes the allowed host from the guard — "
                "the client would reach it directly and the guard would be decorative"
            )
    return problems


def main() -> int:
    problems = asyncio.run(exercise()) + check_render()
    if problems:
        print("  FAIL  the egress guard is not an allowlist")
        for problem in problems:
            print(f"          {problem}")
        return 1
    print("  ok    one host and port admitted, the port, the host, a plain request and a "
          "suffix refused, and the api's clients are pointed at the guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
