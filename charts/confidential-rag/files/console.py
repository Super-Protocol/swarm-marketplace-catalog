#!/usr/bin/env python3
"""The deployment's front door: a sign-in form, a session cookie, and a chat over the corpus.

Why this exists rather than upstream's playground: upstream's production container proxies the
collection and document endpoints to `ingestor-server`, which this port does not deploy —
`ingestor_server` needs `nv_ingest_client` and drives four NIM containers, which is the whole
licence problem this listing exists to avoid. So this serves the same shape over the open
components: ask a question and read the answer with its citations, submit a document and see
what the extractor made of it, plus the two things upstream has no place for — who is allowed
in, and which model and which embedder touched the data.

Four things here are not incidental.

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

**A degraded component is reported, not smoothed over.** The status panel reads the pipeline's
own health report, where a reranker that answered its probe and refused every actual rerank now
says `degraded` with the reason — and the console also keeps the verdict from the last answer it
relayed, because `Citations.reranker` cannot be missed by a worker that did not serve the query.
A reranker doing nothing on every query is what the previous version of this listing shipped,
and the only trace was citations reading 0.0.

stdlib plus what the rag-server image already carries (fastapi, httpx) — this runs in that image,
from a ConfigMap the cloud digests and signs, so the code serving the door is in the evidence
beside the code answering the questions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse

import httpx
from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("console")

PASSWORD = os.environ.get("CONSOLE_PASSWORD", "")
SESSION_KEY = os.environ.get("CONSOLE_SESSION_KEY", "").encode()
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL_SECONDS", 12 * 3600))
COOKIE = "crag_session"

RAG_URL = os.environ.get("RAG_SERVER_URL", "http://127.0.0.1:8081").rstrip("/")
SYNC_URL = os.environ.get("CORPUS_SYNC_URL", "").rstrip("/")
# The indexer authenticates from 0.1.9 — `POST /documents` and `DELETE /documents/<id>` on
# its port are writes into the index whose answers are cited beside the gated corpus, and a
# namespace is not a trust boundary once a second workload is in it (SUP-895). Same Secret,
# same key, both pods.
SYNC_API_KEY = os.environ.get("CORPUS_API_KEY", "")
COLLECTION = os.environ.get("COLLECTION_NAME", "documents")
UPLOAD_COLLECTION = os.environ.get("UPLOAD_COLLECTION_NAME", "")
UI_DIR = os.environ.get("UI_DIR", "/opt/console")
# What a browser may hand over, mirrored from the indexer's own ceiling so the refusal happens
# here rather than after the bytes have crossed the namespace.
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 64 * 1024 * 1024))
# Extraction is one document at a time on four CPU cores: 13.1 s for a fifteen-page PDF with a
# text layer, 52.6 s for the same pages as a 200-dpi scan through OCR, and a queue in front of
# it when somebody submits two.
UPLOAD_TIMEOUT_SECONDS = float(os.environ.get("UPLOAD_TIMEOUT_SECONDS", 960))

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
    "uploadCollection": UPLOAD_COLLECTION,
    "extractionStack": os.environ.get("ABOUT_EXTRACTION_STACK", ""),
    "extractionLicence": os.environ.get("ABOUT_EXTRACTION_LICENCE", ""),
    "uploadsEnabled": bool(UPLOAD_COLLECTION and SYNC_URL),
    "uploadMaxBytes": UPLOAD_MAX_BYTES,
    "sourceRepo": os.environ.get("ABOUT_SOURCE_REPO", ""),
    "expectedDigest": os.environ.get("ABOUT_EXPECTED_DIGEST", ""),
}

# The last thing an answer said about its own reranking. One process, one replica, and no
# store: a restarted console simply has nothing to say until the next answer or the next
# health report, which is the honest state rather than a stale one.
LAST_RERANKING: dict[str, object] = {}

if not PASSWORD or not SESSION_KEY:
    # Refuse to start rather than serve an open door. A console that came up without a
    # password would pass every probe in the chart and publish the corpus to the internet.
    raise SystemExit(
        "console: CONSOLE_PASSWORD and CONSOLE_SESSION_KEY are required; refusing to serve "
        "an unauthenticated interface on a public hostname"
    )

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def sync_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """What the console presents to the indexer. Its bearer token, plus whatever else."""
    headers = dict(extra or {})
    if SYNC_API_KEY:
        headers["Authorization"] = f"Bearer {SYNC_API_KEY}"
    return headers


def document_name_header(name: str) -> str:
    """A submitted file name, in a form an HTTP header carries without losing any of it.

    Percent-encoded, and that is the whole of SUP-895. 0.1.8 sent
    `name.encode("ascii", "replace")`, which turns every character outside ASCII into `?` —
    so `Счёт.pdf` and `План.pdf` both arrived at the indexer as `????.pdf`. The indexer
    derives a document's identity from the name it was given and REPLACES a document of the
    same identity, so the second of those two uploads deleted every chunk of the first, and
    both read `????.pdf` in the console and in every citation quoting them. Cyrillic file
    names are the normal case for this deployment's users, and the submitted copy inside the
    enclave is the only copy.

    A header is latin-1 on the wire, so the name cannot simply be sent as it is. Percent
    encoding is reversible, is ASCII, and leaves nothing for the indexer's filter-expression
    checks to have to care about; `urllib.parse.unquote` on the other side gets the real name
    back before anything is derived from it.
    """
    return urllib.parse.quote(name, safe="")


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
        #
        # `asyncio.sleep` and not `time.sleep`: this handler runs on the event loop, and a
        # blocking sleep here stops the whole console for half a second — including an answer
        # streaming to somebody already signed in. Two wrong passwords a second from anyone who
        # finds the hostname would be a denial of service against every legitimate user, which
        # is a worse bargain than the brute-force cost it buys.
        await asyncio.sleep(0.5)
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


def reranker_panel(pipeline: object) -> dict[str, object]:
    """What to say about reranking, out of the two places that know.

    `degraded` is reported if EITHER the pipeline's health report says so or the last answer
    this console relayed said so. Not "the most recent of the two": a health report from a
    worker that has reranked nothing says `healthy` quite correctly, and letting that
    overwrite a degradation a query actually hit is how this failure stayed invisible in the
    first place. A recovery clears it, because the next successful answer and the next health
    report both say so.
    """
    panel: dict[str, object] = {"degraded": False, "reason": "", "source": "none"}

    if isinstance(pipeline, dict):
        for service in pipeline.get("nim") or []:
            if not isinstance(service, dict):
                continue
            if str(service.get("service") or "").lower() != "ranking":
                continue
            panel["model"] = service.get("model") or ""
            panel["status"] = service.get("status") or ""
            if str(service.get("status") or "").lower() == "degraded":
                panel.update(
                    degraded=True,
                    reason=str(service.get("message") or ""),
                    source="health",
                )
            break

    if LAST_RERANKING.get("degraded") and not panel["degraded"]:
        panel.update(
            degraded=True,
            reason=str(LAST_RERANKING.get("reason") or ""),
            source="answer",
            observedAt=LAST_RERANKING.get("at"),
        )
    return panel


@app.get("/api/status")
async def status(request: Request) -> JSONResponse:
    """What the corpus and the pipeline are doing, as the deployment itself sees it."""
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)

    out: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if SYNC_URL:
            try:
                answer = await client.get(
                    f"{SYNC_URL}/status", headers=sync_headers()
                )
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
    # Two sources for one fact, and both are needed. The health report is the one an operator
    # can read with no query in flight; the verdict carried by the last answer is the one that
    # cannot be missed, because `/v1/health` is served by whichever uvicorn worker takes the
    # request and not necessarily the one that reranked.
    out["reranker"] = reranker_panel(out.get("pipeline"))
    return JSONResponse(out)


def sse_error(message: str) -> StreamingResponse:
    """One server-sent error event, which is what the console's chat renders.

    The alternative — a 4xx with a JSON body — reaches a reader as "the pipeline
    returned no answer", because the page is reading an event stream by then.
    """

    async def once():
        yield f"data: {json.dumps({'error': message})}\n\n".encode()

    return StreamingResponse(
        once(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def live_collections() -> list[str] | None:
    """The collections a question may name, as the indexer sees them.

    `None` means the indexer could not be asked, which is a different answer
    from "there are none": the first is a reason to try the gated collection and
    see, the second is a deployment with nothing indexed, and a question is
    worth attempting in one case and not the other.
    """
    if not SYNC_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            answer = await client.get(
                f"{SYNC_URL}/collections", headers=sync_headers()
            )
        return [
            name
            for name in (answer.json().get("collections") or [])
            if isinstance(name, str) and name
        ]
    except Exception:  # noqa: BLE001
        logger.warning("could not ask the indexer which collections exist")
        return None


def reranking_watcher():
    """A coroutine fed raw SSE bytes, recording what each event said about reranking.

    Written as a generator rather than a parse-at-the-end because the answer is streamed and
    the citations arrive in the middle of it. It keeps only the last verdict: one answer, one
    reranking.
    """
    buffer = ""
    while True:
        chunk = yield
        buffer += chunk.decode("utf-8", "replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                except ValueError:
                    continue
                citations = event.get("citations") if isinstance(event, dict) else None
                report = (citations or {}).get("reranker") if isinstance(citations, dict) else None
                if isinstance(report, dict):
                    LAST_RERANKING.clear()
                    LAST_RERANKING.update(
                        degraded=bool(report.get("degraded")),
                        reason=str(report.get("reason") or ""),
                        scoreSource=str(report.get("score_source") or ""),
                        at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    )
        # A buffer that never completes an event is an answer that never ended; bound it
        # rather than grow it for the life of the request.
        if len(buffer) > 1_000_000:
            buffer = buffer[-4096:]


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

    # Only collections that EXIST may be named: the rag-server answers 400 for the whole
    # query if one does not, rather than skipping it. So the indexer is asked, per question —
    # two existence checks, no embedding — instead of this guessing from configuration, which
    # would be wrong on a deployment whose corpus has not indexed yet and right again ten
    # minutes later.
    collections = await live_collections()
    if collections is None:
        # The indexer could not be asked. Try the gated collection, which is the one a
        # deployment with a dataset attached has.
        collections = [COLLECTION]
    elif not collections:
        # Nothing is indexed at all, and relaying the rag-server's `400 Collection …
        # does not exist. Ensure a collection is created using POST /collection` would
        # tell a tenant to call an endpoint this listing does not publish.
        return sse_error(
            "There is nothing to search yet: no dataset is attached and no document has "
            "been submitted. The panels on the right say which."
        )

    payload = {
        "messages": [{"role": "user", "content": question}],
        "use_knowledge_base": True,
        "collection_names": collections,
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
                # Forwarded byte for byte, and read in passing. The thing being read is
                # `citations.reranker`: whether the answer going past was reranked at all.
                # Recording it here is what lets the status panel say so between queries,
                # which is the one window in which nobody is looking at a citation.
                watcher = reranking_watcher()
                next(watcher)
                async for chunk in answer.aiter_raw():
                    try:
                        watcher.send(chunk)
                    except Exception:  # noqa: BLE001 — never spoil an answer to read it
                        logger.debug("could not read the reranking verdict", exc_info=True)
                    yield chunk

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------------------------
# Documents
#
# The collection and document management upstream's `ingestor-server` provides, over the
# components this port actually deploys: the extraction stage is `swarm-doc-ingest`, and the
# indexer beside this console is what writes the chunks. Every one of these is behind the
# session, because a document a person submits is the same data the corpus is.
# ---------------------------------------------------------------------------------------------


@app.get("/api/documents")
async def list_documents(request: Request) -> JSONResponse:
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not SYNC_URL:
        return JSONResponse({"documents": [], "enabled": False})
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            answer = await client.get(
                f"{SYNC_URL}/documents", headers=sync_headers()
            )
        return JSONResponse(answer.json(), status_code=answer.status_code)
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"error": str(error)}, status_code=502)


@app.post("/api/documents")
async def submit_document(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """Hand one file to the indexer, which extracts it and writes its chunks.

    The bytes are read into memory here rather than streamed through, and that is a bounded
    decision rather than an oversight: `UPLOAD_MAX_BYTES` is the bound, this container has
    512Mi, and the alternative — a chunked relay — would mean this console deciding what to do
    with half an uploaded document when the indexer refuses the second half.
    """
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not (SYNC_URL and UPLOAD_COLLECTION):
        return JSONResponse(
            {
                "error": "this deployment has no extraction service, so it reads documents "
                "from its gated source only"
            },
            status_code=503,
        )

    name = os.path.basename((file.filename or "").replace("\\", "/")).strip()
    if not name:
        return JSONResponse({"error": "the file has no name"}, status_code=400)

    # One byte past the ceiling and no further: the refusal has to happen on bytes already
    # read rather than after the whole upload is resident in a 512Mi container (SUP-893).
    # `UploadFile.read` takes a size, so this costs nothing but the argument.
    blob = await file.read(UPLOAD_MAX_BYTES + 1)
    if not blob:
        return JSONResponse(
            {"error": "an empty document has nothing to index"}, status_code=400
        )
    if len(blob) > UPLOAD_MAX_BYTES:
        # The size is not reported, because this never read it: what is known is that the
        # document is larger than the limit, and the limit is the actionable half.
        return JSONResponse(
            {
                "error": f"{name} is larger than the {UPLOAD_MAX_BYTES} bytes this "
                "deployment accepts"
            },
            status_code=413,
        )

    try:
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            answer = await client.post(
                f"{SYNC_URL}/documents",
                content=blob,
                headers=sync_headers({
                    "Content-Type": "application/octet-stream",
                    "X-Document-Name": document_name_header(name),
                }),
            )
    except httpx.TimeoutException:
        return JSONResponse(
            {
                "error": "extraction did not finish in time. One document is extracted at a "
                "time and the rest queue; the document panel shows it once it lands."
            },
            status_code=504,
        )
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"error": str(error)}, status_code=502)
    try:
        return JSONResponse(answer.json(), status_code=answer.status_code)
    except ValueError:
        return JSONResponse(
            {"error": answer.text[:400] or "the indexer answered nothing"},
            status_code=502,
        )


@app.delete("/api/documents/{upload_id}")
async def remove_document(request: Request, upload_id: str) -> JSONResponse:
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not SYNC_URL:
        return JSONResponse({"error": "no indexer to ask"}, status_code=503)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            answer = await client.delete(
                f"{SYNC_URL}/documents/{upload_id}", headers=sync_headers()
            )
        return JSONResponse(answer.json(), status_code=answer.status_code)
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"error": str(error)}, status_code=502)


# A nonce is generated per process and is not used for anything a reader should trust; it exists
# so a restarted console is distinguishable in its own logs.
logger.info("console up (instance %s)", secrets.token_hex(4))
