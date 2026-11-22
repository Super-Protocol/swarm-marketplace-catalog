#!/usr/bin/env python3
"""The deployment's front door: a sign-in form, a session cookie, and a chat over the corpus.

Why this exists rather than upstream's playground: upstream's production container proxies the
collection and document endpoints to `ingestor-server`, the one service this port has not
substituted yet, so shipping it would publish a console whose document panel cannot work. This
serves the part that does work — ask a question, read the answer, see its citations — plus the
two things upstream has no place for: who is allowed in, and which model and which embedder
touched the data.

Three things here are not incidental.

**The credential is collected with a form and carried in a cookie.** HTTP Basic does not survive
a single-page console: the manifest and the icons are fetched in a mode where the browser sends
no credentials, so the prompt returns for ever, and ingress-nginx strips `Authorization` when it
performs basic auth itself, so the app behind it authenticates nobody.

**The session is a signed assertion, not a row.** `HMAC-SHA256(key, "v1|<expiry>")`, verified in
constant time. No store to run, no state to lose on a restart, and nothing per-deployment in any
rendered object: the key is in a Secret, which the platform lifts out before the evidence
snapshot is taken.

**Only `/healthz` is outside the session.** It reports liveness and nothing about the corpus,
because it is reachable from the public hostname by anyone who finds the name.

stdlib plus what the rag-server image already carries (fastapi, httpx) — this runs in that image,
from a ConfigMap the cloud digests and signs, so the code serving the door is in the evidence
beside the code answering the questions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time

import httpx
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("console")

PASSWORD = os.environ.get("CONSOLE_PASSWORD", "")
SESSION_KEY = os.environ.get("CONSOLE_SESSION_KEY", "").encode()
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL_SECONDS", 12 * 3600))
COOKIE = "crag_session"

RAG_URL = os.environ.get("RAG_SERVER_URL", "http://127.0.0.1:8081").rstrip("/")
SYNC_URL = os.environ.get("CORPUS_SYNC_URL", "").rstrip("/")
COLLECTION = os.environ.get("COLLECTION_NAME", "documents")
UI_DIR = os.environ.get("UI_DIR", "/opt/console")

# What the deployment says about itself. Every value is rendered from the listing's own
# parameters, so the panel a user reads and the digest the cloud signed describe the same thing.
ABOUT = {
    "title": os.environ.get("CONSOLE_TITLE", "Confidential RAG"),
    "generationModel": os.environ.get("ABOUT_GENERATION_MODEL", ""),
    "generationLicence": os.environ.get("ABOUT_GENERATION_LICENCE", ""),
    "embeddingModel": os.environ.get("ABOUT_EMBEDDING_MODEL", ""),
    "embeddingLicence": os.environ.get("ABOUT_EMBEDDING_LICENCE", ""),
    "rerankerModel": os.environ.get("ABOUT_RERANKER_MODEL", ""),
    "rerankerLicence": os.environ.get("ABOUT_RERANKER_LICENCE", ""),
    "collection": COLLECTION,
    "sourceRepo": os.environ.get("ABOUT_SOURCE_REPO", ""),
    "expectedDigest": os.environ.get("ABOUT_EXPECTED_DIGEST", ""),
}

if not PASSWORD or not SESSION_KEY:
    # Refuse to start rather than serve an open door. A console that came up without a
    # password would pass every probe in the chart and publish the corpus to the internet.
    raise SystemExit(
        "console: CONSOLE_PASSWORD and CONSOLE_SESSION_KEY are required; refusing to serve "
        "an unauthenticated interface on a public hostname"
    )

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def issue(ttl: int = SESSION_TTL) -> str:
    expiry = int(time.time()) + ttl
    payload = f"v1|{expiry}"
    mac = hmac.new(SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{mac}"


def valid(token: str | None) -> bool:
    if not token:
        return False
    parts = token.split("|")
    if len(parts) != 3 or parts[0] != "v1":
        return False
    payload, mac = f"{parts[0]}|{parts[1]}", parts[2]
    expected = hmac.new(SESSION_KEY, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return False
    try:
        return int(parts[1]) > time.time()
    except ValueError:
        return False


def signed_in(request: Request) -> bool:
    return valid(request.cookies.get(COOKIE))


def page(name: str) -> str:
    with open(os.path.join(UI_DIR, name), encoding="utf-8") as handle:
        return handle.read()


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0e14;color:#e6e6e6;
      font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
 form{width:min(92vw,22rem);padding:2rem;background:#141925;border:1px solid #232a3a;border-radius:12px}
 h1{margin:0 0 .25rem;font-size:1.15rem}
 p{margin:0 0 1.5rem;color:#8b94a7;font-size:.86rem}
 label{display:block;margin-bottom:.4rem;font-size:.8rem;color:#8b94a7}
 input{width:100%;box-sizing:border-box;padding:.6rem .7rem;background:#0b0e14;color:#e6e6e6;
       border:1px solid #2b3448;border-radius:8px;font-size:.95rem}
 button{margin-top:1.1rem;width:100%;padding:.65rem;border:0;border-radius:8px;background:#3b82f6;
        color:#fff;font-size:.95rem;font-weight:600;cursor:pointer}
 .err{margin:.9rem 0 0;color:#f87171;font-size:.84rem}
</style></head>
<body><form method="post" action="/login">
 <h1>__TITLE__</h1>
 <p>This deployment answers questions over your own documents. Sign in to continue.</p>
 <label for="password">Access password</label>
 <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
 <button type="submit">Sign in</button>
 __ERROR__
</form></body></html>
"""


def login_page(error: str = "") -> HTMLResponse:
    markup = LOGIN_PAGE.replace("__TITLE__", ABOUT["title"])
    markup = markup.replace(
        "__ERROR__", '<p class="err">That password was not accepted.</p>' if error else ""
    )
    # 401 on a failed attempt, 200 on a first visit: a monitor should be able to tell the
    # difference without parsing the body.
    return HTMLResponse(markup, status_code=401 if error else 200)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness, and nothing about the corpus. Reachable by anybody who finds the hostname."""
    return JSONResponse({"status": "ok"})


@app.get("/")
async def root(request: Request) -> HTMLResponse:
    if not signed_in(request):
        return login_page()
    return HTMLResponse(page("index.html"))


@app.post("/login")
async def login(response: Response, password: str = Form("")) -> Response:
    # Constant-time, so a wrong answer takes as long as a right one.
    if not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        # A deliberate, fixed delay. Not rate limiting — one process cannot do that honestly —
        # but enough that an online guess against this door is not free.
        time.sleep(0.5)
        return login_page(error="bad")
    redirect = RedirectResponse("/", status_code=303)
    redirect.set_cookie(
        COOKIE,
        issue(),
        max_age=SESSION_TTL,
        httponly=True,
        # The deployment is served over TLS by the cloud's gateway and on no other scheme.
        secure=True,
        samesite="lax",
        path="/",
    )
    return redirect


@app.post("/logout")
async def logout() -> Response:
    redirect = RedirectResponse("/", status_code=303)
    redirect.delete_cookie(COOKIE, path="/")
    return redirect


@app.get("/api/about")
async def about(request: Request) -> JSONResponse:
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    return JSONResponse(ABOUT)


@app.get("/api/status")
async def status(request: Request) -> JSONResponse:
    """What the corpus and the pipeline are doing, as the deployment itself sees it."""
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)

    out: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if SYNC_URL:
            try:
                answer = await client.get(f"{SYNC_URL}/status")
                out["corpus"] = answer.json()
            except Exception as error:  # noqa: BLE001 — the panel reports, it does not fail
                out["corpus"] = {"error": str(error)}
        try:
            # `check_dependencies` is what makes this answer about the four endpoints rather
            # than about the server's own process — without it the report is three empty
            # lists, which is a panel that always says nothing.
            answer = await client.get(
                f"{RAG_URL}/v1/health", params={"check_dependencies": "true"}
            )
            out["pipeline"] = answer.json()
        except Exception as error:  # noqa: BLE001
            out["pipeline"] = {"error": str(error)}
    return JSONResponse(out)


@app.post("/api/ask")
async def ask(request: Request) -> Response:
    """Put a question to the ported pipeline: retrieve, rerank, generate, cite.

    Proxied rather than called from the browser, for one reason that matters: the rag-server is
    on no Ingress and has no authentication of its own, so the only thing standing between the
    retriever and the internet is this session check. A browser-side call would need the
    rag-server published.
    """
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "expected a JSON body"}, status_code=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)
    if len(question) > 8000:
        return JSONResponse({"error": "question is too long"}, status_code=413)

    payload = {
        "messages": [{"role": "user", "content": question}],
        "use_knowledge_base": True,
        "collection_names": [COLLECTION],
        "enable_citations": True,
        "stream": True,
    }

    async def relay():
        timeout = httpx.Timeout(10.0, read=300.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{RAG_URL}/v1/generate", json=payload) as answer:
                if answer.status_code != 200:
                    detail = (await answer.aread()).decode("utf-8", "replace")[:2000]
                    yield f"data: {json.dumps({'error': detail})}\n\n".encode()
                    return
                async for chunk in answer.aiter_raw():
                    yield chunk

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# A nonce is generated per process and is not used for anything a reader should trust; it exists
# so a restarted console is distinguishable in its own logs.
logger.info("console up (instance %s)", secrets.token_hex(4))
