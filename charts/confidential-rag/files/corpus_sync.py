#!/usr/bin/env python3
"""Index the bound document source into the vector store, and report what the gate decided.

This is the half of the listing that the gatekeeper story actually runs through, so it is worth
being explicit about what it is and is not.

**What it is.** A loop. Read the document rows from whatever the deployment's `corpus` slot is
bound to, chunk them, embed them with the deployment's own embedder, and write them into the
collection the rag-server retrieves from. Then wait, re-read the source's own count and
high-water mark, and re-index only when those moved.

**Why it reads ClickHouse over HTTP and not object storage.** Because that is the only thing
the marketplace gatekeeper can carry. The gate injects `x-clickhouse-user` / `x-clickhouse-key`
into every request it forwards and rewrites the database the caller named, so a gated dataset
reaches this deployment as a plain local address with no credential in it at all — which is the
whole point: a consumer who reads every object in their own namespace learns the shape of the
data and no way to reach it outside the gate. A document corpus in a bucket is not gateable
today, whatever a dataset's `access.mode` says.

**Why the verdict is a first-class output and not a log line.** Withdrawing approval is the
thing the gate exists to do, and from inside the deployment it looks like an HTTP error on the
next read. If that only reached the log, a tenant whose access was cut would see answers quietly
get worse. So every attempt's verdict — `admitted`, `refused`, or `pending` — is served on
`/status`, with the gate's own message, and the console shows it.

**Why the collection is created through the rag-server's own code.** `MilvusVDB` and its
`get_langchain_vectorstore` decide the field names, the index parameters, the token derivation
and the search parameters. Writing the collection with anything else would mean this file and
the retriever agreeing about the schema by coincidence; importing the retriever's own path means
they cannot disagree. It is also what keeps `nv-ingest` out of the picture: upstream's
`create_collection` needs `nv_ingest_client`, and `langchain_milvus` creating the collection
from the first batch is the documented alternative the rag-server's citation code already
handles ("if langchain is used for ingestion, the source is a string").
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests
from langchain_core.documents import Document

from nvidia_rag.utils.configuration import NvidiaRAGConfig
from nvidia_rag.utils.embedding import get_embedding_model
from nvidia_rag.utils.vdb.milvus.milvus_vdb import MilvusVDB

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("corpus-sync")

COLLECTION = os.environ.get("COLLECTION_NAME", "documents")
BINDING_PATH = os.environ.get("CORPUS_BINDING_FILE", "/etc/corpus/binding.json")
TABLE = os.environ.get("CORPUS_TABLE", "documents")
COL_ID = os.environ.get("CORPUS_ID_COLUMN", "doc_id")
COL_TITLE = os.environ.get("CORPUS_TITLE_COLUMN", "title")
COL_TEXT = os.environ.get("CORPUS_TEXT_COLUMN", "text")
COL_UPDATED = os.environ.get("CORPUS_UPDATED_COLUMN", "updated_at")
CHUNK_CHARS = int(os.environ.get("CORPUS_CHUNK_CHARS", 1800))
CHUNK_OVERLAP = int(os.environ.get("CORPUS_CHUNK_OVERLAP_CHARS", 200))
ROW_LIMIT = int(os.environ.get("CORPUS_ROW_LIMIT", 20000))
POLL_SECONDS = int(os.environ.get("CORPUS_POLL_SECONDS", 60))
BATCH = int(os.environ.get("CORPUS_EMBED_BATCH", 16))
STATUS_PORT = int(os.environ.get("CORPUS_STATUS_PORT", 8085))
HTTP_TIMEOUT = float(os.environ.get("CORPUS_HTTP_TIMEOUT", 60))

# Everything `/status` reports, replaced wholesale under the lock after each attempt.
STATE: dict[str, Any] = {
    "verdict": "pending",
    "message": "no read attempted yet",
    "sources": [],
    "documents": None,
    "chunks": None,
    "lastAttemptAt": None,
    "lastSuccessAt": None,
    "collection": COLLECTION,
}
STATE_LOCK = threading.Lock()


def publish(**fields: Any) -> None:
    with STATE_LOCK:
        STATE.update(fields)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------------------
# The bound source
# --------------------------------------------------------------------------------------


def bindings() -> list[dict[str, Any]]:
    """The resolved `corpus` slot, as the marketplace wrote it into the Secret.

    A slot with `multiple: true` resolves to a list. An empty or absent file is a supported
    state and not an error: a deployment with nothing attached is a deployment waiting for its
    data owner to approve it, which is exactly the state the gate is for.
    """
    try:
        with open(BINDING_PATH, encoding="utf-8") as handle:
            raw = handle.read().strip()
    except FileNotFoundError:
        return []
    if not raw:
        return []
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        # A single binding, or the `{count, items}` shape — accept both rather than guess.
        if "items" in parsed and isinstance(parsed["items"], list):
            return [item for item in parsed["items"] if isinstance(item, dict)]
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def connection(binding: dict[str, Any]) -> dict[str, Any]:
    return binding.get("connection") or {}


def is_gated(binding: dict[str, Any]) -> bool:
    """A gated binding carries no password, because the gate holds the publisher's own.

    `connection.host` is `<releasePrefix>-gatekeeper-egress` and `username` is the literal
    `gateway`, which the gate replaces before it forwards. So the absence of a password is the
    reliable signal, not the hostname.
    """
    return not connection(binding).get("password")


def endpoint(binding: dict[str, Any]) -> str:
    conn = connection(binding)
    scheme = "https" if conn.get("tls") else "http"
    return f"{scheme}://{conn.get('host')}:{int(conn.get('port') or 8123)}/"


def query(binding: dict[str, Any], sql: str) -> list[dict[str, Any]]:
    """One ClickHouse HTTP query, through the gate when the dataset is gated.

    The database is passed as a query parameter rather than written into the SQL: the gate
    rewrites the database the caller named, and a name baked into the statement is a name it
    cannot rewrite.
    """
    conn = connection(binding)
    params = {"database": conn.get("database") or "default", "default_format": "JSONEachRow"}
    headers: dict[str, str] = {"Content-Type": "text/plain; charset=utf-8"}
    if not is_gated(binding):
        # An ungated dataset hands over the owner's own read-only credentials.
        headers["x-clickhouse-user"] = str(conn.get("username") or "")
        headers["x-clickhouse-key"] = str(conn.get("password") or "")

    url = endpoint(binding) + "?" + urllib.parse.urlencode(params)
    response = requests.post(url, data=sql.encode("utf-8"), headers=headers, timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        # ClickHouse and the gate both answer with a readable body. It is the most useful thing
        # a tenant whose access was just withdrawn can be shown, so it is carried, trimmed.
        raise RuntimeError(
            f"{response.status_code} from {conn.get('host')}: "
            f"{response.text.strip()[:400] or 'no message'}"
        )
    rows = []
    for line in response.text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def quote(identifier: str) -> str:
    """A ClickHouse identifier, back-quoted. Refuses anything that is not one."""
    if not identifier.replace("_", "").isalnum():
        raise ValueError(
            f"{identifier!r} is not an identifier: the table and column names come from the "
            "listing's parameters and are not a place to build SQL from"
        )
    return f"`{identifier}`"


def watermark(binding: dict[str, Any]) -> tuple[int, str]:
    """How many rows the source has and how recently it changed — two numbers, one query.

    Re-reading these is what makes the loop cheap: a corpus that has not moved is not
    re-embedded, and a corpus that has is picked up without anybody triggering anything.
    """
    sql = (
        f"SELECT count() AS rows, max({quote(COL_UPDATED)}) AS high "
        f"FROM {quote(TABLE)}"
    )
    rows = query(binding, sql)
    if not rows:
        return 0, ""
    return int(rows[0].get("rows") or 0), str(rows[0].get("high") or "")


def documents(binding: dict[str, Any]) -> list[dict[str, Any]]:
    sql = (
        f"SELECT {quote(COL_ID)} AS id, {quote(COL_TITLE)} AS title, {quote(COL_TEXT)} AS text "
        f"FROM {quote(TABLE)} "
        f"ORDER BY {quote(COL_UPDATED)} DESC "
        f"LIMIT {int(ROW_LIMIT)}"
    )
    return query(binding, sql)


# --------------------------------------------------------------------------------------
# Chunking and indexing
# --------------------------------------------------------------------------------------


def chunks(text: str) -> list[str]:
    """Fixed-width character chunks with an overlap, split on a boundary where one is near.

    Deliberately not a token splitter. The embedder truncates anything over its own window
    server-side (`--auto-truncate`), the chunk width here is set from the model's window in the
    listing, and a character split needs no tokenizer in this container — which is one fewer
    thing that has to agree with the model being served.
    """
    text = (text or "").strip()
    if not text:
        return []
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            # Prefer a paragraph, then a sentence, then a word — but only if one is in the
            # last fifth of the window, so a document without them still makes progress.
            window = text[start:end]
            floor = int(CHUNK_CHARS * 0.8)
            for marker in ("\n\n", ". ", "\n", " "):
                cut = window.rfind(marker)
                if cut >= floor:
                    end = start + cut + len(marker)
                    break
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [chunk for chunk in out if chunk]


def vector_store():
    """The retriever's own vector store, built through the retriever's own code path."""
    config = NvidiaRAGConfig()
    embedder = get_embedding_model(
        model=config.embeddings.model_name,
        url=config.embeddings.server_url,
        config=config,
    )
    vdb = MilvusVDB(
        collection_name=COLLECTION,
        milvus_uri=config.vector_store.url,
        embedding_model=embedder,
        config=config,
        dense_dim=config.embeddings.dimensions,
        # CPU Milvus. GPU_CAGRA needs a card this pod does not have and must not take.
        gpu_index=False,
        gpu_search=False,
        username=config.vector_store.username,
        password=(
            config.vector_store.password.get_secret_value()
            if config.vector_store.password is not None
            else ""
        ),
    )
    return vdb, vdb.get_langchain_vectorstore(COLLECTION)


def index(rows: list[dict[str, Any]], binding: dict[str, Any]) -> int:
    """Rebuild the collection from these rows. Returns the number of chunks written.

    A rebuild rather than an update, and the reason is the gate: a document the owner deleted
    has to leave the index, and a diff against a source we may be refused access to tomorrow is
    a diff we cannot trust. Dropping and rewriting a corpus of this size costs a minute.
    """
    vdb, store = vector_store()
    conn = connection(binding)
    dataset = binding.get("dataset") or "corpus"
    database = conn.get("database") or "default"

    if vdb.check_collection_exists(COLLECTION):
        logger.info("dropping the previous %s collection before the rebuild", COLLECTION)
        vdb.delete_collections([COLLECTION])
        # The store was built against the old collection; build it again so langchain
        # re-creates the collection from the first batch.
        vdb, store = vector_store()

    pending: list[Document] = []
    written = 0
    for row in rows:
        doc_id = str(row.get("id") or "")
        title = str(row.get("title") or "")
        pieces = chunks(str(row.get("text") or ""))
        for position, piece in enumerate(pieces):
            pending.append(
                Document(
                    page_content=piece,
                    metadata={
                        # A string, which is the shape the rag-server's citation code reads
                        # when langchain did the ingestion. It names where the chunk came
                        # from in a form a reader can act on.
                        "source": f"{dataset}:{database}.{TABLE}#{doc_id}",
                        "title": title,
                        "content_metadata": {
                            "type": "text",
                            # Rows have no pages. Zero is what the citation model defaults
                            # to and what the console then does not display.
                            "page_number": 0,
                            "chunk": position,
                        },
                    },
                )
            )
        if len(pending) >= BATCH:
            store.add_documents(pending)
            written += len(pending)
            pending = []
    if pending:
        store.add_documents(pending)
        written += len(pending)
    return written


# --------------------------------------------------------------------------------------
# The loop and the status endpoint
# --------------------------------------------------------------------------------------


class Status(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's own naming
        if self.path.split("?")[0] not in ("/status", "/healthz"):
            self.send_error(404)
            return
        with STATE_LOCK:
            body = json.dumps(STATE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("status %s", fmt % args)


def describe(bound: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "dataset": binding.get("dataset"),
            "title": binding.get("title"),
            "interface": binding.get("interface"),
            "database": connection(binding).get("database"),
            "table": TABLE,
            "gated": is_gated(binding),
        }
        for binding in bound
    ]


def loop() -> None:
    last: tuple[int, str] | None = None
    while True:
        bound = bindings()
        publish(sources=describe(bound), lastAttemptAt=now())

        if not bound:
            publish(
                verdict="pending",
                message=(
                    "no dataset is attached to the corpus slot — attach one and this "
                    "deployment indexes it without being restarted"
                ),
                documents=0,
                chunks=0,
            )
            time.sleep(POLL_SECONDS)
            continue

        # One source today. The slot accepts several and the loop would index each into the
        # same collection; indexing the first is what is measured, so it is what is done.
        binding = bound[0]
        try:
            mark = watermark(binding)
        except Exception as error:  # noqa: BLE001 — the verdict is the product here
            logger.warning("the source refused the read: %s", error)
            publish(verdict="refused", message=str(error))
            time.sleep(POLL_SECONDS)
            continue

        if mark == last:
            publish(
                verdict="admitted",
                message=f"{mark[0]} rows, unchanged since the last read",
                lastSuccessAt=now(),
            )
            time.sleep(POLL_SECONDS)
            continue

        try:
            rows = documents(binding)
            written = index(rows, binding)
        except Exception as error:  # noqa: BLE001
            logger.exception("indexing failed")
            publish(verdict="refused" if "clickhouse" in str(error).lower() else "pending",
                    message=str(error)[:400])
            time.sleep(POLL_SECONDS)
            continue

        last = mark
        logger.info("indexed %d chunks from %d documents", written, len(rows))
        publish(
            verdict="admitted",
            message=f"{mark[0]} rows read and indexed",
            documents=len(rows),
            chunks=written,
            lastSuccessAt=now(),
        )
        time.sleep(POLL_SECONDS)


def main() -> None:
    server = HTTPServer(("0.0.0.0", STATUS_PORT), Status)
    threading.Thread(target=server.serve_forever, name="status", daemon=True).start()
    logger.info("status on :%d, polling every %ds", STATUS_PORT, POLL_SECONDS)
    loop()


if __name__ == "__main__":
    main()
