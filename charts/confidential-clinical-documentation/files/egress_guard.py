#!/usr/bin/env python3
"""The one way out of this deployment, and in this listing there is none.

This file is shared with `confidential-vulnerability-analysis`, where it admits exactly one
external host. Here its allowlist is **empty**, and that is the point: a deployment holding
recorded consultations should reach nothing at all, and an allowlist with no entries is a claim
a reader can check rather than a sentence they have to believe. Every request — any host, any
port, plain HTTP included — is refused with a 403 and counted.

The scribe needs nothing external. Its recognition and diarization models are inside its image,
pinned by sha256; its generation endpoint is a Service in this namespace; its transcript corpus,
when one is attached, is reached through the marketplace gatekeeper at a local address. There is
no model host, no vendor API and no telemetry destination to admit.

Three choices in here are deliberate.

**It is a CONNECT proxy and nothing else.** A plain proxied request (``GET http://host/path``)
is refused, so there is no cleartext path out at all. It is an allowlist, not an inspection
point, and it holds no certificate for anything.

**The host and the port are matched exactly.** No wildcards, no suffix matching: a wildcard is
how an allowlist stops being one. The hostname is resolved HERE, after the match, so a refused
request does not even cause a DNS lookup — which is itself a disclosure to whoever runs the
resolver.

**It is code in a ConfigMap rather than a configuration file for a third-party proxy.** The
cloud digests and signs the manifests, so this file is inside the deployment's published
evidence: the rule, and the thing that enforces the rule, are the same artefact and a verifier
reads it rather than taking a squid.conf and a container image on trust.

Why this exists at all rather than a NetworkPolicy: a cluster space is provisioned with the
platform's own policy set, which admits ``0.0.0.0/0`` minus the private ranges, and Kubernetes
NetworkPolicies are additive — a policy a chart renders can only widen what is permitted. A
listing therefore cannot narrow its namespace's egress, and the enforcement point available to
it is in front of the client.

stdlib only, so it runs in the scribe image with nothing installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

LISTEN_ADDRESS = os.environ.get("EGRESS_LISTEN_ADDRESS", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("EGRESS_LISTEN_PORT", "3128"))
ALLOW_HOST = os.environ.get("EGRESS_ALLOW_HOST", "")
ALLOW_PORT = int(os.environ.get("EGRESS_ALLOW_PORT", "443"))

# A request line plus headers. Anything past this is not a proxy request a client of ours sends.
MAX_HEADER_BYTES = 8192
# How long a client may take to send its request line.
REQUEST_TIMEOUT_SECONDS = 30.0
# How long an established tunnel may sit with no bytes in either direction. An OSV lookup is one
# request and one response.
IDLE_TIMEOUT_SECONDS = 120.0
RELAY_CHUNK_BYTES = 65536

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s egress-guard %(message)s",
)
logger = logging.getLogger("egress-guard")

STATUS: dict[str, object] = {
    "allowHost": ALLOW_HOST,
    "allowPort": ALLOW_PORT,
    # Explicit, so a reader of `/_guard/status` does not have to infer a deny-all policy from
    # an empty string that could equally be a value somebody forgot to set.
    "policy": "refuse-everything" if not ALLOW_HOST else "allow-one-host",
    "admitted": 0,
    "refused": 0,
    "lastRefused": "",
    "lastRefusedAt": "",
    "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}

# Served on loopback, to the console in the same pod, so an operator can see what this process
# decided without reading a log. It is the only non-CONNECT request this process answers.
STATUS_PATH = "/_guard/status"


def _refuse_response(reason: str) -> bytes:
    body = json.dumps({"error": "refused by the deployment's egress guard", "reason": reason})
    return (
        "HTTP/1.1 403 Forbidden\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    ).encode()


def _status_response() -> bytes:
    body = json.dumps(STATUS)
    return (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    ).encode()


def _record_refusal(target: str) -> None:
    STATUS["refused"] = int(STATUS["refused"]) + 1  # type: ignore[arg-type]
    STATUS["lastRefused"] = target
    STATUS["lastRefusedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_target(request_line: str) -> tuple[str, str, str]:
    """Split a request line into (method, host, port). Non-CONNECT lines return empty host."""
    parts = request_line.split()
    if len(parts) < 2:
        return ("", "", "")
    method, target = parts[0].upper(), parts[1]
    if method != "CONNECT":
        return (method, "", "")
    host, _, port = target.rpartition(":")
    if not host or not port:
        return (method, "", "")
    return (method, host, port)


def admitted(host: str, port: str) -> bool:
    """Exact host, exact port. Nothing else, and no normalisation that could widen either."""
    if not ALLOW_HOST:
        return False
    if host != ALLOW_HOST:
        return False
    try:
        return int(port) == ALLOW_PORT
    except ValueError:
        return False


async def _relay(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await asyncio.wait_for(src.read(RELAY_CHUNK_BYTES), IDLE_TIMEOUT_SECONDS)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        dst.close()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        try:
            head = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), REQUEST_TIMEOUT_SECONDS
            )
        except asyncio.IncompleteReadError as error:
            head = error.partial
        except (asyncio.TimeoutError, asyncio.LimitOverrunError):
            writer.close()
            return

        if len(head) > MAX_HEADER_BYTES:
            _record_refusal("oversized request")
            writer.write(_refuse_response("the request head is larger than this guard accepts"))
            await writer.drain()
            writer.close()
            return

        request_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")

        if request_line.startswith("GET " + STATUS_PATH):
            writer.write(_status_response())
            await writer.drain()
            writer.close()
            return

        method, host, port = parse_target(request_line)
        if not host:
            # A plain proxied request, or something that is not a proxy request at all. Refused
            # rather than forwarded: there is no cleartext path out of this deployment.
            _record_refusal(request_line[:200])
            logger.warning("refused: %s", request_line[:200])
            writer.write(
                _refuse_response(
                    f"only CONNECT to {ALLOW_HOST}:{ALLOW_PORT} is admitted; "
                    f"{method or 'that'} is not"
                )
            )
            await writer.drain()
            writer.close()
            return

        if not admitted(host, port):
            _record_refusal(f"{host}:{port}")
            logger.warning("refused: CONNECT %s:%s", host, port)
            writer.write(
                _refuse_response(
                    f"{host}:{port} is not the one host this deployment may reach "
                    f"({ALLOW_HOST}:{ALLOW_PORT})"
                )
            )
            await writer.drain()
            writer.close()
            return

        try:
            # Resolved here, after the match: a refused request causes no lookup either.
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(ALLOW_HOST, ALLOW_PORT), REQUEST_TIMEOUT_SECONDS
            )
        except Exception as error:  # noqa: BLE001 — any failure is one answer to the client
            logger.error("upstream %s:%s unreachable: %s", ALLOW_HOST, ALLOW_PORT, error)
            writer.write(
                _refuse_response(f"{ALLOW_HOST}:{ALLOW_PORT} could not be reached: {error}")
            )
            await writer.drain()
            writer.close()
            return

        STATUS["admitted"] = int(STATUS["admitted"]) + 1  # type: ignore[arg-type]
        logger.info("admitted: CONNECT %s:%s", host, port)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _relay(reader, upstream_writer),
            _relay(upstream_reader, writer),
        )
    except Exception as error:  # noqa: BLE001 — one bad connection is not the guard's life
        logger.error("connection failed: %s", error)
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    # Modified by Super Protocol, 2026, for this listing, and the difference from the
    # vulnerability-analysis copy of this file is one line of policy.
    #
    # There, an empty allowlist is a misconfiguration: that deployment needs public
    # vulnerability intelligence, every lookup would fail with a 403, and the reports would say
    # "no data" without saying why — so the guard refuses to start and the chart refuses to
    # render. HERE AN EMPTY ALLOWLIST IS THE CONFIGURATION. This deployment reaches nothing:
    # its models are in its image, its generation endpoint is a Service in its namespace, and
    # its transcript corpus arrives through the gatekeeper at a local address. The chart
    # refuses an allowlist with an ENTRY in it, which is the opposite validation.
    #
    # So the guard starts, says what it is, and refuses everything. The alternative — not
    # running it at all — would leave the deployment's egress claim resting on the absence of a
    # container rather than on a rule in the signed evidence.
    if not ALLOW_HOST:
        logger.info("the allowlist is empty: every request will be refused, by design")

    server = await asyncio.start_server(handle, LISTEN_ADDRESS, LISTEN_PORT)
    logger.info(
        "listening on %s:%s — admitting CONNECT %s:%s and nothing else",
        LISTEN_ADDRESS,
        LISTEN_PORT,
        ALLOW_HOST,
        ALLOW_PORT,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
