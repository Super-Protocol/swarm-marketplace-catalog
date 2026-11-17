#!/usr/bin/env python3
"""The one assumption behind several engines on one hostname, measured.

The chart serves a second model at `https://<host>/<name>/v1` and starts that
engine with `--root-path /<name>`. Nothing rewrites the path on the way in: the
Ingress forwards `/gemma/v1/chat/completions` verbatim, and the engine is
expected to both route it *and* authenticate it.

The second half is the one that matters. vLLM guards a request by comparing its
path against `("/v1", "/v2", "/inference")` — so an engine that routed the
prefixed path without stripping the prefix first would serve
`/gemma/v1/chat/completions` to anyone, with no `Authorization` header, on a
public hostname. That is the exact failure `charts/swarm-model-server` exists to
make impossible, it renders identically either way, and a readiness probe passes
on both.

It holds because of two strips, one in each layer:

  * `AuthenticationMiddleware.__call__` does
    `scope["path"].removeprefix(scope["root_path"])` before the prefix
    comparison — `vllm/entrypoints/openai/server_utils.py` at v0.22.0, the
    appVersion this chart pins.
  * Starlette's router matches `get_route_path(scope)`, which strips
    `root_path` as well — so the route is found at `/v1/...`.

and because FastAPI copies `app.root_path` into every request's scope
(`api_server.py` sets it from `--root-path`), which is what both strips read.

So the middleware below is vLLM's, copied verbatim, and the app is driven as an
ASGI app with no test client: three layers' versions decide the answer, and the
point is to notice when any of them changes it.

    charts/tests/root_path_guard.py

Needs `fastapi` (which brings Starlette); skipped with a note where it is not
installed. The versions that serve are the image's, not this machine's — what
this catches is the semantics moving underneath a future engine bump.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sys

try:
    import fastapi
    import starlette
    from starlette.datastructures import Headers
    from starlette.responses import JSONResponse
except ModuleNotFoundError:
    print("  note  fastapi is not installed, so the --root-path behaviour is unchecked")
    sys.exit(0)

PREFIX = "/gemma"
KEY = "TESTKEYtestkey0123456789abcdefAB"

# vllm/entrypoints/openai/server_utils.py @ v0.22.0, verbatim. Copied rather
# than imported because importing it needs the engine, and what is under test is
# these few lines against the framework beneath them.
GUARDED_PREFIX = ("/v1", "/v2", "/inference")


class AuthenticationMiddleware:
    def __init__(self, app, tokens: list[str]) -> None:
        self.app = app
        self.api_tokens = [hashlib.sha256(t.encode("utf-8")).digest() for t in tokens]

    def verify_token(self, headers) -> bool:
        authorization_header_value = headers.get("Authorization")
        if not authorization_header_value:
            return False
        scheme, _, param = authorization_header_value.partition(" ")
        if scheme.lower() != "bearer":
            return False
        param_hash = hashlib.sha256(param.encode("utf-8")).digest()
        token_match = False
        for token_hash in self.api_tokens:
            token_match |= secrets.compare_digest(param_hash, token_hash)
        return token_match

    def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("method") == "OPTIONS":
            return self.app(scope, receive, send)
        root_path = scope.get("root_path", "")
        url_path = scope["path"].removeprefix(root_path)
        headers = Headers(scope=scope)
        if url_path.startswith(GUARDED_PREFIX) and not self.verify_token(headers):
            response = JSONResponse(content={"error": "Unauthorized"}, status_code=401)
            return response(scope, receive, send)
        return self.app(scope, receive, send)


def build():
    """An app shaped like the engine's: the routes that matter, nothing else."""
    app = fastapi.FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return {"served": "to whoever reaches it"}

    @app.get("/v1/models")
    def models():
        return {"data": [{"id": "gemma-2-2b-it"}]}

    # api_server.py: `app.root_path = args.root_path`, then the middleware.
    app.root_path = PREFIX
    app.add_middleware(AuthenticationMiddleware, tokens=[KEY])
    return app


async def status_of(app, path: str, *, key: str | None = None) -> int:
    """One request, as an ASGI call. No test client, so no extra dependency."""
    headers = [(b"host", b"models.example")]
    if key is not None:
        headers.append((b"authorization", f"Bearer {key}".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 50000), "server": ("models.example", 443),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def main() -> int:
    app = build()
    cases = [
        ("the prefixed /v1 answers with the key", f"{PREFIX}/v1/models", KEY, 200),
        ("the prefixed /v1 is 401 without it", f"{PREFIX}/v1/models", None, 401),
        ("the prefixed /v1 is 401 with the wrong key", f"{PREFIX}/v1/models", "NOPE", 401),
        ("the unprefixed /v1 is still guarded", "/v1/models", None, 401),
        # The probe talks to the container port, where there is no prefix.
        ("/health answers unprefixed, which is what the probe uses", "/health", None, 200),
        # Which is also why the Ingress publishes <prefix>/v1 and not <prefix>/:
        # the engine serves its open routes under the prefix as well.
        ("/metrics is served under the prefix too, so it must stay unpublished",
         f"{PREFIX}/metrics", None, 200),
        ("a doubled prefix reaches nothing", f"{PREFIX}{PREFIX}/v1/models", None, 404),
        ("another engine's prefix reaches nothing", "/llama/v1/models", None, 404),
    ]
    failures = []
    for label, path, key, expected in cases:
        got = asyncio.run(status_of(app, path, key=key))
        if got == expected:
            print(f"  ok    {label}")
        else:
            failures.append(f"{label}: {path} -> {got}, expected {expected}")
    for message in failures:
        print(f"  FAIL  {message}")
    if failures:
        return 1
    print(f"--root-path routes and authenticates on fastapi {fastapi.__version__}, "
          f"starlette {starlette.__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
