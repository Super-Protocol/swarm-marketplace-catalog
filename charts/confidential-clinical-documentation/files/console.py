#!/usr/bin/env python3
"""The deployment's front door: a sign-in form, a session cookie, and a proxy to the scribe.

What is behind this door is recorded patient-clinician conversation and the clinical notes
drawn from it — protected health information in the strictest sense, which is why there is a
door at all. Upstream has none: its API and its UI are published on port 80 with no
authentication of any kind, and the API's CORS policy grants credentials to every origin.

Five things here are not incidental.

**The credential is collected with a form and carried in a cookie.** HTTP Basic does not
survive a single-page console: the manifest and the icons are fetched in a mode where the
browser sends no credentials, so the prompt returns for ever, and ingress-nginx strips
`Authorization` when it performs basic auth itself, so the app behind it authenticates nobody.

**The session is a signed assertion, not a row.** `HMAC-SHA256(key, "v1|<expiry>")`, verified
in constant time. No store to run, no state to lose on a restart, and nothing per-deployment in
any rendered object: the key is in a Secret, which the platform lifts out before the evidence
snapshot is taken.

**The scribe is on loopback and on no Service.** It is reachable from this process and from
nothing else in the cluster — not from another pod, let alone from the internet — so this
session check is the whole of what stands in front of it, and that is the only reason it is safe
for the scribe to have no authentication of its own.

**This is a streaming proxy, not a redirect.** Transcription and note generation are
server-sent-event streams that run for minutes, and a recording is an upload of up to half a
gigabyte. Both are relayed chunk by chunk in both directions, with buffering off, so a
transcript appears sentence by sentence through the door rather than arriving in one lump when
the stream closes.

**Nothing leaves.** The egress guard beside this process admits no host at all — its allowlist
is empty, which is a statement a reader can check rather than take. `/_console/guard` reports
what it has refused.

Only `/healthz` is outside the session. It reports liveness and nothing about any consultation.

stdlib plus what the scribe image already carries (fastapi, httpx, uvicorn) — this runs in that
image, from a ConfigMap the cloud digests and signs, so the code serving the door is in the
evidence beside the code doing the work.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

import httpx
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s console %(message)s",
)
logger = logging.getLogger("console")

PASSWORD = os.environ.get("CONSOLE_PASSWORD", "")
SESSION_KEY = os.environ.get("CONSOLE_SESSION_KEY", "").encode()
SESSION_TTL = int(os.environ.get("CONSOLE_SESSION_TTL_SECONDS", 12 * 3600))
COOKIE = "ccd_session"

SCRIBE_URL = os.environ.get("SCRIBE_API_URL", "http://127.0.0.1:8000").rstrip("/")
GUARD_URL = os.environ.get("EGRESS_GUARD_URL", "").rstrip("/")

# A transcription is minutes of CPU work on a long recording, and a note generation is an LLM
# stream; neither should be cut off by a proxy timeout. The connect timeout stays short because
# a scribe that is not listening should be reported at once rather than waited for.
UPSTREAM_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=float(os.environ.get("PROXY_READ_TIMEOUT_SECONDS", 3600)),
    write=float(os.environ.get("PROXY_WRITE_TIMEOUT_SECONDS", 900)),
    pool=10.0,
)
RELAY_CHUNK_BYTES = 65536

# ---------------------------------------------------------------------------
# The gated transcript corpus
# ---------------------------------------------------------------------------
# The resolved `transcripts` data slot, as JSON, from a Secret. For a gated dataset it carries
# a plain local address and NO credential — the gate injects the publisher's own and rewrites
# the database the caller named — so a consumer who reads every object in their namespace
# learns the shape of the data and no way to reach it outside the gate.
CORPUS_BINDING = os.environ.get("CORPUS_BINDING_JSON", "")
CORPUS_TABLE = os.environ.get("CORPUS_TABLE", "transcripts")
CORPUS_ID_COLUMN = os.environ.get("CORPUS_ID_COLUMN", "transcript_id")
CORPUS_SPEAKER_COLUMN = os.environ.get("CORPUS_SPEAKER_COLUMN", "speaker")
CORPUS_START_COLUMN = os.environ.get("CORPUS_START_COLUMN", "start_seconds")
CORPUS_END_COLUMN = os.environ.get("CORPUS_END_COLUMN", "end_seconds")
CORPUS_TEXT_COLUMN = os.environ.get("CORPUS_TEXT_COLUMN", "text")
CORPUS_RECORDED_COLUMN = os.environ.get("CORPUS_RECORDED_AT_COLUMN", "recorded_at")
CORPUS_LIST_LIMIT = int(os.environ.get("CORPUS_LIST_LIMIT", 500))
CORPUS_SEGMENT_LIMIT = int(os.environ.get("CORPUS_SEGMENT_LIMIT", 20000))
CORPUS_TIMEOUT = float(os.environ.get("CORPUS_TIMEOUT_SECONDS", 60))

# A corpus identifier is interpolated into a SQL string literal, so it is validated here
# rather than escaped there. Anything outside this alphabet is refused, not quoted.
CORPUS_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:@/#-]{0,190}$")

# What the deployment says about itself. Every value is rendered from the listing's own
# parameters, so the panel a reader sees and the digest the cloud signed describe the same thing.
ABOUT: dict[str, Any] = {
    "title": os.environ.get("CONSOLE_TITLE", "Confidential Clinical Documentation"),
    "generationModel": os.environ.get("ABOUT_GENERATION_MODEL", ""),
    "generationLicence": os.environ.get("ABOUT_GENERATION_LICENCE", ""),
    "asrModel": os.environ.get("ABOUT_ASR_MODEL", ""),
    "asrLicence": os.environ.get("ABOUT_ASR_LICENCE", ""),
    "diarizationModels": os.environ.get("ABOUT_DIARIZATION_MODELS", ""),
    "diarizationLicences": os.environ.get("ABOUT_DIARIZATION_LICENCES", ""),
    "diarizationFloors": {
        "assignedWordFraction": os.environ.get("DIARIZATION_FLOOR_ASSIGNED_WORD_FRACTION", ""),
        "segmentPurity": os.environ.get("DIARIZATION_FLOOR_SEGMENT_PURITY", ""),
        "speechCoverage": os.environ.get("DIARIZATION_FLOOR_SPEECH_COVERAGE", ""),
    },
    "audioRetentionHours": os.environ.get("AUDIO_RETENTION_HOURS", ""),
    "egressAllowHost": os.environ.get("EGRESS_ALLOW_HOST", ""),
    "sourceRepo": os.environ.get("ABOUT_SOURCE_REPO", ""),
    "upstream": os.environ.get("ABOUT_UPSTREAM", ""),
    "clinicalReviewRequired": True,
}

if not PASSWORD or not SESSION_KEY:
    # Refuse to start rather than serve an open door. A console that came up without a
    # password would pass every probe in the chart and publish a clinical archive.
    raise SystemExit(
        "console: CONSOLE_PASSWORD and CONSOLE_SESSION_KEY are required; refusing to serve "
        "an unauthenticated interface on a public hostname"
    )

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _open_client() -> None:
    global _client
    # One connection pool for the life of the process. `trust_env=False` so no HTTP_PROXY
    # from the pod's environment can redirect a consultation anywhere.
    _client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, trust_env=False,
                                follow_redirects=False)


@app.on_event("shutdown")
async def _close_client() -> None:
    if _client is not None:
        await _client.aclose()


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------
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


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1117;color:#e8edf2;
      font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
 form{width:min(92vw,23rem);padding:2rem;background:#161b22;border:1px solid #2a323c;border-radius:12px}
 h1{margin:0 0 .25rem;font-size:1.15rem}
 p{margin:0 0 1.5rem;color:#9aa7b4;font-size:.86rem}
 label{display:block;margin-bottom:.4rem;font-size:.8rem;color:#9aa7b4}
 input{width:100%;box-sizing:border-box;padding:.6rem .7rem;background:#0d1117;color:#e8edf2;
       border:1px solid #2a323c;border-radius:8px;font-size:.95rem}
 button{margin-top:1.1rem;width:100%;padding:.65rem;border:0;border-radius:8px;background:#2dd4bf;
        color:#0d1117;font-size:.95rem;font-weight:600;cursor:pointer}
 .err{margin:.9rem 0 0;color:#f85149;font-size:.84rem}
 .note{margin:1.4rem 0 0;color:#6b7885;font-size:.76rem}
</style></head>
<body><form method="post" action="/login">
 <h1>__TITLE__</h1>
 <p>This deployment holds recorded consultations and the notes drawn from them. Sign in to continue.</p>
 <label for="password">Access password</label>
 <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
 <button type="submit">Sign in</button>
 __ERROR__
 <p class="note">Notes produced here are drafts for a clinician to read, correct and sign.</p>
</form></body></html>
"""


def login_page(error: bool = False) -> HTMLResponse:
    markup = LOGIN_PAGE.replace("__TITLE__", ABOUT["title"])
    markup = markup.replace(
        "__ERROR__", '<p class="err">That password was not accepted.</p>' if error else ""
    )
    # 401 on a failed attempt, 200 on a first visit: a monitor should be able to tell the
    # difference without parsing the body.
    return HTMLResponse(markup, status_code=401 if error else 200)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness, and nothing about any consultation. Reachable by anybody who finds the name."""
    return JSONResponse({"status": "ok"})


@app.post("/login")
async def login(password: str = Form("")) -> Response:
    # Constant-time, so a wrong answer takes as long as a right one.
    if not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        # A deliberate, fixed delay — not rate limiting, which one process cannot do honestly,
        # but enough that an online guess against this door is not free. `asyncio.sleep` and
        # not `time.sleep`: this handler runs on the event loop, and blocking it would stop the
        # whole console for every wrong password, uploads and streams included.
        await asyncio.sleep(0.5)
        return login_page(error=True)
    redirect = RedirectResponse("/", status_code=303)
    redirect.set_cookie(COOKIE, issue(), max_age=SESSION_TTL, httponly=True,
                        # Served over TLS by the cloud's gateway and on no other scheme.
                        secure=True, samesite="lax", path="/")
    return redirect


@app.post("/logout")
async def logout() -> Response:
    redirect = RedirectResponse("/", status_code=303)
    redirect.delete_cookie(COOKIE, path="/")
    return redirect


# ---------------------------------------------------------------------------
# What the deployment is
# ---------------------------------------------------------------------------
@app.get("/_console/about")
async def about(request: Request) -> JSONResponse:
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    # `corpusBound` is resolved here rather than at import, and that is not tidiness: the
    # listing renders an UNBOUND slot as the JSON document `[]`, which is a non-empty string.
    # Testing the string said "a corpus is attached" on every deployment that had none, while
    # `/_console/corpus` beside it correctly said `unbound`. A panel that contradicts the thing
    # it is a summary of is worse than no panel.
    return JSONResponse({**ABOUT, "corpusBound": bool(_binding())})


@app.get("/_console/guard")
async def guard(request: Request) -> JSONResponse:
    """What the egress guard has admitted and refused. Its allowlist is empty by design."""
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not GUARD_URL or _client is None:
        return JSONResponse({"enabled": False})
    try:
        answer = await _client.get(f"{GUARD_URL}/_guard/status", timeout=5.0)
        return JSONResponse({"enabled": True, **answer.json()})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"enabled": True, "error": str(exc)[:200]})


# ---------------------------------------------------------------------------
# The gated transcript corpus
# ---------------------------------------------------------------------------
def _binding() -> dict[str, Any]:
    if not CORPUS_BINDING.strip():
        return {}
    try:
        parsed = json.loads(CORPUS_BINDING)
    except json.JSONDecodeError:
        logger.error("the corpus binding is not JSON; the slot is treated as unbound")
        return {}
    # The slot is `multiple: true`, so the binding is a list; the first entry is used and the
    # console says which one it read.
    if isinstance(parsed, list):
        return parsed[0] if parsed else {}
    if isinstance(parsed, dict):
        entries = parsed.get("datasets") or parsed.get("items")
        if isinstance(entries, list):
            return entries[0] if entries else {}
        return parsed
    return {}


def _connection(binding: dict[str, Any]) -> dict[str, Any]:
    return binding.get("connection") or {}


async def _query(sql: str) -> list[dict[str, Any]]:
    """One ClickHouse HTTP query, through the gate when the dataset is gated.

    The database travels as a query parameter rather than inside the SQL: the gate rewrites the
    database the caller named, and a name baked into the statement is a name it cannot rewrite.
    A gated binding carries no password — the gate holds the publisher's own — so the absence
    of one, not the hostname, is what decides whether credentials are sent.
    """
    binding = _binding()
    conn = _connection(binding)
    if not conn.get("host"):
        raise RuntimeError("no transcript corpus is attached to this deployment")
    scheme = "https" if conn.get("tls") else "http"
    url = (f"{scheme}://{conn['host']}:{int(conn.get('port') or 8123)}/?"
           + urllib.parse.urlencode({"database": conn.get("database") or "default",
                                     "default_format": "JSONEachRow"}))
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if conn.get("password"):
        headers["x-clickhouse-user"] = str(conn.get("username") or "")
        headers["x-clickhouse-key"] = str(conn.get("password"))

    assert _client is not None
    answer = await _client.post(url, content=sql.encode("utf-8"), headers=headers,
                                timeout=CORPUS_TIMEOUT)
    if answer.status_code != 200:
        # ClickHouse and the gate both answer with a readable body, and it is the most useful
        # thing a tenant whose access was just withdrawn can be shown. Carried, trimmed.
        raise RuntimeError(f"{answer.status_code} from {conn['host']}: "
                           f"{answer.text.strip()[:400] or 'no message'}")
    return [json.loads(line) for line in answer.text.splitlines() if line.strip()]


@app.get("/_console/corpus")
async def corpus(request: Request) -> JSONResponse:
    """The consultations this deployment is currently allowed to read."""
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    binding = _binding()
    if not binding:
        return JSONResponse({"bound": False, "verdict": "unbound", "transcripts": [],
                             "message": "No transcript corpus is attached. Attach one with a "
                                        "reconfigure, or upload a recording instead."})
    try:
        rows = await _query(
            f"SELECT {CORPUS_ID_COLUMN} AS id, count() AS segments, "
            f"min({CORPUS_RECORDED_COLUMN}) AS recorded_at "
            f"FROM {CORPUS_TABLE} GROUP BY {CORPUS_ID_COLUMN} "
            f"ORDER BY recorded_at DESC LIMIT {CORPUS_LIST_LIMIT}")
    except Exception as exc:  # noqa: BLE001
        # A withdrawn approval looks exactly like this, and it is a first-class answer rather
        # than a log line: without it a tenant whose access was cut sees an empty list.
        return JSONResponse({"bound": True, "verdict": "refused", "transcripts": [],
                             "message": str(exc)[:400]}, status_code=200)
    return JSONResponse({"bound": True, "verdict": "admitted",
                         "dataset": binding.get("name") or binding.get("id") or "",
                         "transcripts": rows})


@app.post("/_console/corpus/{corpus_id}/import")
async def corpus_import(corpus_id: str, request: Request) -> JSONResponse:
    """Read one consultation from the corpus and hand it to the scribe as a transcript."""
    if not signed_in(request):
        return JSONResponse({"error": "not signed in"}, status_code=401)
    if not CORPUS_ID_PATTERN.match(corpus_id):
        return JSONResponse({"error": "that is not a transcript identifier"}, status_code=400)
    binding = _binding()
    if not binding:
        return JSONResponse({"error": "no transcript corpus is attached"}, status_code=409)

    quoted = corpus_id.replace("\\", "\\\\").replace("'", "\\'")
    try:
        rows = await _query(
            f"SELECT {CORPUS_START_COLUMN} AS start, {CORPUS_END_COLUMN} AS end, "
            f"{CORPUS_TEXT_COLUMN} AS text, {CORPUS_SPEAKER_COLUMN} AS speaker, "
            f"toString({CORPUS_RECORDED_COLUMN}) AS recorded_at "
            f"FROM {CORPUS_TABLE} WHERE {CORPUS_ID_COLUMN} = '{quoted}' "
            f"ORDER BY {CORPUS_START_COLUMN} LIMIT {CORPUS_SEGMENT_LIMIT}")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)[:400]}, status_code=502)
    if not rows:
        return JSONResponse({"error": "no segments for that identifier"}, status_code=404)

    payload = {
        "source": (binding.get("name") or binding.get("id") or "the attached corpus"),
        "name": corpus_id,
        "recorded_at": rows[0].get("recorded_at") or "",
        "segments": [{"start": float(r.get("start") or 0), "end": float(r.get("end") or 0),
                      "text": str(r.get("text") or ""), "speaker": r.get("speaker")}
                     for r in rows if str(r.get("text") or "").strip()],
    }
    assert _client is not None
    answer = await _client.post(f"{SCRIBE_URL}/api/transcribe/import", json=payload)
    return JSONResponse(_json_or_text(answer), status_code=answer.status_code)


def _json_or_text(answer: httpx.Response) -> Any:
    try:
        return answer.json()
    except ValueError:
        return {"error": answer.text.strip()[:400] or "no message"}


# ---------------------------------------------------------------------------
# Everything else is the scribe, behind the session
# ---------------------------------------------------------------------------
# Hop-by-hop headers, which belong to one connection and must not be forwarded.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
              "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


@app.api_route("/{path:path}",
               methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    if not signed_in(request):
        # A browser asking for a page gets the sign-in form; anything else — the UI's own
        # `fetch`, a stream that outlived its session — gets a 401 it can act on, because an
        # HTML login page arriving where JSON was expected is a parse error, not a prompt.
        wants_html = "text/html" in (request.headers.get("accept") or "")
        if request.method in ("GET", "HEAD") and wants_html:
            return login_page()
        return JSONResponse({"error": "not signed in"}, status_code=401)

    url = f"{SCRIBE_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    # The session cookie is this door's, not the scribe's; the scribe has no use for it and no
    # reason to see it.
    headers.pop("cookie", None)

    assert _client is not None
    upstream = _client.build_request(
        request.method, url, headers=headers,
        # Streamed, not read: a recording is up to half a gigabyte and reading it into this
        # process first would double the memory for no purpose.
        content=request.stream() if request.method in ("POST", "PUT", "PATCH") else None,
    )
    try:
        answer = await _client.send(upstream, stream=True)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "the scribe is not answering yet; it loads its models on first use"},
            status_code=503)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"the scribe did not answer: {exc}"}, status_code=502)

    out_headers = {k: v for k, v in answer.headers.items() if k.lower() not in HOP_BY_HOP}
    # Server-sent events reach the browser as they are produced, which is the whole point of a
    # transcript that appears sentence by sentence. Without this ingress-nginx buffers the
    # response and the UI sits blank until the stream closes.
    if "text/event-stream" in (answer.headers.get("content-type") or ""):
        out_headers["X-Accel-Buffering"] = "no"
        out_headers["Cache-Control"] = "no-cache"

    async def relay():
        try:
            async for chunk in answer.aiter_raw(RELAY_CHUNK_BYTES):
                yield chunk
        finally:
            await answer.aclose()

    return StreamingResponse(relay(), status_code=answer.status_code, headers=out_headers)
