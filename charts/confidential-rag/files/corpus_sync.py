#!/usr/bin/env python3
"""Index what this deployment is allowed to read, and report what the gate decided.

Two sources, two collections, one indexer. That shape is the first thing to know, and it is not
tidiness:

* the **gated source** — a ClickHouse document table the data owner approved — is REBUILT on
  every change: dropped and rewritten, because a document the owner deleted has to leave the
  index and a diff against a source we may be refused tomorrow is a diff we cannot trust;
* **uploaded documents** — a file a person put through the console, extracted by
  `swarm-doc-ingest` — are ADDITIVE and live in a collection of their own.

One collection for both would mean every refresh of the gated corpus silently deleted every
uploaded file. The rag-server retrieves from both collections in one query, so the split costs
nothing a reader of the answers can see.

**What it is.** A loop plus a small HTTP service. Read the document rows from whatever the
deployment's `corpus` slot is bound to, chunk them, embed them with the deployment's own embedder,
and write them into the collection the rag-server retrieves from. Then wait, re-read the source's
own count and high-water mark, and re-index only when those moved. Beside that, serve the console's
document management: submit a file, list what has been submitted, remove one.

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

**Why every chunk carries the same metadata keys, including the empty ones.**
`langchain_milvus` creates the collection's fields from the FIRST batch it is given, so a key
that appears only on a later document is a key the collection has no field for. A table chunk
therefore carries an empty `image_metadata` and a text chunk carries an empty `table_metadata`,
and that is load-bearing rather than sloppy.

**What an uploaded document gets indexed, and what it does not.** `swarm-doc-ingest` returns
nv-ingest elements: text, tables as cell grids and Markdown, and pictures with their bounding
boxes. Text and tables are indexed and searchable — a table as its Markdown, split on row
boundaries with its header repeated, because a table cut in half mid-grid is a chunk no model can
read. A **picture is located, not transcribed**: this stack reads no chart data, so a chart is
indexed by its caption and its position, and a chart with no caption is counted and not indexed
at all. Writing a chunk that claimed to be a chart's contents would be the same kind of lie as
the reranker scores this listing's previous version shipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# Uploaded files, in a collection of their own. The gated corpus is rebuilt from
# scratch whenever it moves, so one collection for both would mean a refresh of
# the corpus deleting every file anybody submitted.
UPLOAD_COLLECTION = os.environ.get("UPLOAD_COLLECTION_NAME", "")
INGEST_URL = os.environ.get("INGEST_URL", "").rstrip("/")
INGEST_API_KEY = os.environ.get("INGEST_API_KEY", "")
# Extraction is CPU work on a pod that does one document at a time and queues the
# rest: a 15-page 200-dpi scan through OCR is 52.6 s measured, and a queue in
# front of it makes that a wait rather than a failure.
INGEST_TIMEOUT = float(os.environ.get("INGEST_TIMEOUT_SECONDS", 900))
UPLOAD_MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 64 * 1024 * 1024))
# How many chunk rows the listing and the delete will read in one query. A document of a
# hundred pages is a few hundred chunks, so this is two hundred such documents.
UPLOAD_LIST_LIMIT = int(os.environ.get("UPLOAD_LIST_LIMIT", 16384))
# What this service issues as a document identifier, and therefore all it will accept: the
# value is interpolated into a Milvus filter expression.
UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
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
# How long one batch of chunks may take to embed and write before this gives up on it and
# reports a stall. Not a performance knob: it is the difference between a slow reindex and a
# loop that is wedged, and without it there is no way to tell them apart from outside.
BATCH_TIMEOUT = float(os.environ.get("CORPUS_BATCH_TIMEOUT", 300))
# Passed to the embedding client. The OpenAI client defaults to ten minutes with two retries,
# so one unanswered call can hold this loop for half an hour with nothing to show for it.
EMBED_TIMEOUT = float(os.environ.get("CORPUS_EMBED_TIMEOUT", 120))

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
    # Whether this deployment accepts a file at all, so the console can say
    # "not configured" rather than offering a button that answers 503.
    "uploads": {
        "enabled": bool(UPLOAD_COLLECTION and INGEST_URL),
        "collection": UPLOAD_COLLECTION,
        "maxBytes": UPLOAD_MAX_BYTES,
    },
}
STATE_LOCK = threading.Lock()

# One embedder, two cores, and two things that want it: the gated corpus rebuild
# and an upload. They write different collections, so they do not race over the
# index — but a batch that holds the embedder for half a minute is a batch its
# own health endpoint cannot answer during, which is how an earlier version of
# this file turned a reindex into a loop that never finished. So they take turns.
INDEX_LOCK = threading.Lock()


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


def vector_store(collection: str = ""):
    """The retriever's own vector store, built through the retriever's own code path."""
    collection = collection or COLLECTION
    config = NvidiaRAGConfig()
    embedder = get_embedding_model(
        model=config.embeddings.model_name,
        url=config.embeddings.server_url,
        config=config,
    )
    # The retriever's own factory does not take a timeout, and its default is the OpenAI
    # client's: ten minutes, twice. Set it here rather than forking the factory — a longer
    # wait than this is not patience, it is a loop nobody can see into.
    for attribute, value in (("request_timeout", EMBED_TIMEOUT), ("max_retries", 1)):
        try:
            setattr(embedder, attribute, value)
        except Exception:  # noqa: BLE001 — an older client may not carry the field
            logger.warning("could not set %s on the embedding client", attribute)
    vdb = MilvusVDB(
        collection_name=collection,
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
    return vdb, vdb.get_langchain_vectorstore(collection)


def write_batch(store: Any, batch: list[Document]) -> None:
    """One batch, in a thread, so a wedged client is a reported stall and not a silence.

    `add_documents` embeds and inserts. Either half can hang — the embedding client on a
    saturated endpoint, the insert on a flush — and both held this loop with `/status` frozen
    at the attempt that started it, which is the one state an operator cannot act on. Observed
    on the gpu-blueprints cloud: twelve minutes inside one rebuild with the embedder idle and
    nothing published.
    """
    error: list[BaseException] = []

    def run() -> None:
        try:
            store.add_documents(batch)
        except BaseException as problem:  # noqa: BLE001 — re-raised on the caller's thread
            error.append(problem)

    worker = threading.Thread(target=run, name="embed-batch", daemon=True)
    worker.start()
    worker.join(BATCH_TIMEOUT)
    if worker.is_alive():
        raise TimeoutError(
            f"a batch of {len(batch)} chunks did not finish embedding and writing within "
            f"{BATCH_TIMEOUT:.0f}s — the endpoint is saturated or the index is not accepting"
        )
    if error:
        raise error[0]


def write_all(store: Any, documents: list[Document], progress=None) -> int:
    """Embed and write every document, in batches, reporting after each one."""
    pending: list[Document] = []
    written = 0
    for document in documents:
        pending.append(document)
        if len(pending) >= BATCH:
            write_batch(store, pending)
            written += len(pending)
            pending = []
            if progress is not None:
                progress(written)
    if pending:
        write_batch(store, pending)
        written += len(pending)
        if progress is not None:
            progress(written)
    return written


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

    documents: list[Document] = []
    for row in rows:
        doc_id = str(row.get("id") or "")
        title = str(row.get("title") or "")
        for position, piece in enumerate(chunks(str(row.get("text") or ""))):
            documents.append(
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

    # Published per batch, not at the end. A rebuild of a real corpus takes minutes, and a
    # status that only moves when it finishes is a status that says nothing while the only
    # interesting thing is happening.
    def progress(written: int) -> None:
        publish(chunks=written, message=f"indexing: {written} chunks written")

    return write_all(store, documents, progress)


# --------------------------------------------------------------------------------------
# Uploaded documents
# --------------------------------------------------------------------------------------
#
# A file a person submits through the console, extracted by `swarm-doc-ingest` — layout,
# table structure and OCR on open components, CPU-only, no NIM and no NGC credential — and
# indexed into a collection of its own.
#
# `swarm-doc-ingest` answers `POST /v1/extract` with nv-ingest `MetadataSchema` elements, and
# the three kinds are not treated alike:
#
#   text        indexed as text, chunked like any prose;
#   structured  a table. Indexed as the Markdown rendering, which is what a model reads well,
#               split on ROW boundaries with the header repeated — a table cut mid-grid is a
#               chunk nothing can read. `table_metadata.table_location` carries the box;
#   image       a picture or a chart. LOCATED, NOT TRANSCRIBED: this stack reads no chart
#               data, so what is indexed is the caption and the position, and a picture with
#               no caption is counted and not indexed at all. A chunk claiming to hold a
#               chart's numbers would be a lie of exactly the kind this version exists to fix.


def uploads_enabled() -> bool:
    return bool(UPLOAD_COLLECTION and INGEST_URL)


def safe_name(name: str) -> str:
    """A submitted filename, reduced to something that can be stored and displayed.

    The name arrives from a browser. It reaches a Milvus field, a log line and the console's
    citation list, so the path is dropped, control characters go, and the length is bounded.
    Refused rather than silently renamed if nothing is left: a document stored as `_` is a
    document nobody can find again.
    """
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = "".join(character for character in base if character.isprintable())
    base = base.replace('"', "'")
    if not base or base in (".", ".."):
        raise ValueError(f"{name!r} is not a usable file name")
    return base[:200]


def extract(filename: str, blob: bytes) -> dict[str, Any]:
    """One document through `swarm-doc-ingest`, returning its elements.

    The key is a bearer token even though the service answers only inside this namespace: a
    namespace is not a trust boundary once a second workload is in it.
    """
    headers = {}
    if INGEST_API_KEY:
        headers["Authorization"] = f"Bearer {INGEST_API_KEY}"
    response = requests.post(
        f"{INGEST_URL}/v1/extract",
        files={"file": (filename, blob, "application/octet-stream")},
        headers=headers,
        timeout=INGEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{response.status_code} from the extraction service: "
            f"{response.text.strip()[:400] or 'no message'}"
        )
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("elements"), list):
        raise RuntimeError(
            "the extraction service answered something that is not "
            "{source_id, source_name, elements, stats}"
        )
    return body


def table_chunks(markdown: str) -> list[str]:
    """A Markdown table, in pieces a model can still read.

    The header and its separator go onto every piece. Without that, the second half of a
    twenty-row table is a grid of numbers with no column names — which embeds, retrieves and
    answers questions wrongly rather than failing.
    """
    lines = [line for line in (markdown or "").splitlines() if line.strip()]
    if len(lines) <= 2:
        return [markdown.strip()] if markdown.strip() else []
    header, separator, rows = lines[0], lines[1], lines[2:]
    preamble = f"{header}\n{separator}\n"
    if len(markdown) <= CHUNK_CHARS:
        return [markdown.strip()]

    out: list[str] = []
    current = preamble
    for row in rows:
        if len(current) + len(row) + 1 > CHUNK_CHARS and current != preamble:
            out.append(current.rstrip())
            current = preamble
        current += row + "\n"
    if current != preamble:
        out.append(current.rstrip())
    return out


def element_documents(
    elements: list[dict[str, Any]], upload_id: str, filename: str
) -> tuple[list[Document], dict[str, int]]:
    """nv-ingest elements to chunks, and a count of what became of each kind.

    Every chunk carries the same metadata KEYS, empty ones included: `langchain_milvus`
    creates the collection's fields from the first batch, so a key that first appears on a
    later document has no field to go into.
    """
    counts = {
        "text": 0,
        "tables": 0,
        "charts": 0,
        "chartsIndexed": 0,
        "chunks": 0,
    }
    documents: list[Document] = []

    def add(
        content: str,
        *,
        kind: str,
        subtype: str,
        page: int,
        position: int,
        table_metadata: dict[str, Any],
        image_metadata: dict[str, Any],
        searchable: bool,
    ) -> None:
        documents.append(
            Document(
                page_content=content,
                metadata={
                    # A string, which is the shape the rag-server's citation code reads when
                    # langchain did the ingestion.
                    "source": f"upload:{filename}#{upload_id}",
                    "title": filename,
                    # Scalars, so the listing and the delete can filter on them.
                    "upload_id": upload_id,
                    "document_name": filename,
                    "content_metadata": {
                        # `text`, because that is the branch of the rag-server's citation
                        # code that cites content it already has. The real kind of the
                        # element is beside it, and the console shows that.
                        "type": "text",
                        "page_number": page,
                        "chunk": position,
                        "element_type": kind,
                        "subtype": subtype,
                        # False for a picture: what is indexed is its caption, not the data
                        # drawn in it. The console and the listing both say so.
                        "searchable_content": searchable,
                    },
                    "table_metadata": table_metadata,
                    "image_metadata": image_metadata,
                },
            )
        )

    position = 0
    for element in elements:
        if not isinstance(element, dict):
            continue
        content_metadata = element.get("content_metadata") or {}
        kind = str(content_metadata.get("type") or "")
        page = int(content_metadata.get("page_number") or 0)
        table_metadata = element.get("table_metadata") or {}
        image_metadata = element.get("image_metadata") or {}
        content = str(element.get("content") or "")

        if kind == "structured":
            counts["tables"] += 1
            for piece in table_chunks(table_metadata.get("table_content") or content):
                add(
                    piece,
                    kind="structured",
                    subtype=str(content_metadata.get("subtype") or "table"),
                    page=page,
                    position=position,
                    table_metadata={
                        "table_location": list(table_metadata.get("table_location") or []),
                        "table_location_max_dimensions": list(
                            table_metadata.get("table_location_max_dimensions") or []
                        ),
                        "caption": str(table_metadata.get("caption") or ""),
                    },
                    image_metadata={},
                    searchable=True,
                )
                position += 1
            continue

        if kind == "image":
            counts["charts"] += 1
            # `content` for a picture is a base64 crop when the extractor is asked for one,
            # and is never text. The caption is the only thing here that can be embedded.
            caption = str(
                image_metadata.get("caption") or content_metadata.get("description") or ""
            ).strip()
            if not caption:
                continue
            counts["chartsIndexed"] += 1
            add(
                caption,
                kind="image",
                subtype="",
                page=page,
                position=position,
                table_metadata={},
                image_metadata={
                    "image_location": list(image_metadata.get("image_location") or []),
                    "image_location_max_dimensions": list(
                        image_metadata.get("image_location_max_dimensions") or []
                    ),
                    "caption": caption,
                },
                searchable=False,
            )
            position += 1
            continue

        if not content.strip():
            continue
        counts["text"] += 1
        for piece in chunks(content):
            add(
                piece,
                kind="text",
                subtype="",
                page=page,
                position=position,
                table_metadata={},
                image_metadata={},
                searchable=True,
            )
            position += 1

    counts["chunks"] = len(documents)
    return documents, counts


def store_upload(filename: str, blob: bytes) -> dict[str, Any]:
    """Extract, chunk and index one submitted file. Returns what became of it.

    Under `INDEX_LOCK`: this and the gated rebuild share one embedder with two cores, and a
    batch that holds them both for half a minute is a batch its own health endpoint cannot
    answer during.
    """
    if not uploads_enabled():
        raise RuntimeError(
            "this deployment has no extraction service configured, so it reads documents "
            "from its gated source only"
        )
    if len(blob) > UPLOAD_MAX_BYTES:
        raise ValueError(
            f"{filename} is {len(blob)} bytes and the limit is {UPLOAD_MAX_BYTES}"
        )

    # The identifier is the NAME's hash and not the content's, so submitting a corrected
    # version of a document replaces it instead of leaving two copies of nearly the same
    # text competing in every search. The extractor's own `source_id` is a per-request
    # uuid and would make every resubmission a new document.
    upload_id = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:32]
    extracted = extract(filename, blob)
    documents, counts = element_documents(
        extracted.get("elements") or [], upload_id, filename
    )
    if not documents:
        raise ValueError(
            f"nothing in {filename} could be indexed: the extractor found "
            f"{counts['charts']} picture(s) with no caption and no text or tables at all"
        )

    with INDEX_LOCK:
        # Replace rather than add to: submitting the same file twice should leave one copy,
        # not two sets of chunks competing in every search.
        removed = remove_upload(upload_id)
        _, store = vector_store(UPLOAD_COLLECTION)
        written = write_all(store, documents)

    logger.info(
        "indexed %s: %d chunks (%d text, %d tables, %d pictures of which %d captioned)%s",
        filename,
        written,
        counts["text"],
        counts["tables"],
        counts["charts"],
        counts["chartsIndexed"],
        f", replacing {removed} chunks of an earlier submission" if removed else "",
    )
    return {
        "id": upload_id,
        "name": filename,
        "chunks": written,
        "replaced": removed,
        "elements": counts,
        "stats": extracted.get("stats") or {},
        "at": now(),
    }


def upload_client():
    """The Milvus client for the uploads collection, or None if there is not one yet.

    `MilvusVDB.get_documents` is not used for the listing below: it reads a document-info
    collection that upstream's ingestor writes and this listing never does, so it would
    answer an empty list for documents that are in the index.
    """
    vdb, _ = vector_store(UPLOAD_COLLECTION)
    if not vdb.check_collection_exists(UPLOAD_COLLECTION):
        return None
    return vdb


def uploaded_documents() -> list[dict[str, Any]]:
    """What has been submitted, read back out of the index itself.

    The index is the registry on purpose: a list held in this pod's memory would be empty
    after a restart while the chunks were still being searched, which is a console that
    disagrees with the answers beside it.
    """
    if not uploads_enabled():
        return []
    try:
        vdb = upload_client()
        if vdb is None:
            return []
        rows = vdb._client.query(  # noqa: SLF001 — see upload_client
            collection_name=UPLOAD_COLLECTION,
            filter='upload_id != ""',
            output_fields=["upload_id", "document_name"],
            limit=UPLOAD_LIST_LIMIT,
        )
    except Exception as error:  # noqa: BLE001 — the panel reports, it does not fail
        logger.warning("could not list the uploaded documents: %s", error)
        return []

    counted: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("upload_id") or "")
        if not key:
            continue
        entry = counted.setdefault(
            key, {"id": key, "name": str(row.get("document_name") or ""), "chunks": 0}
        )
        entry["chunks"] += 1
    return sorted(counted.values(), key=lambda entry: entry["name"].lower())


def remove_upload(upload_id: str) -> int:
    """Drop every chunk of one submitted document. Returns how many went.

    The identifier is checked rather than escaped: it is interpolated into a Milvus filter
    expression, and a filter is as much a query language as SQL is.
    """
    if not UPLOAD_ID_PATTERN.match(upload_id or ""):
        raise ValueError(
            f"{upload_id!r} is not a document identifier — 1 to 64 characters of "
            "[A-Za-z0-9_-], which is what this service issues"
        )
    vdb = upload_client()
    if vdb is None:
        return 0
    before = vdb._client.query(  # noqa: SLF001 — see upload_client
        collection_name=UPLOAD_COLLECTION,
        filter=f'upload_id == "{upload_id}"',
        output_fields=["upload_id"],
        limit=UPLOAD_LIST_LIMIT,
    )
    if not before:
        return 0
    vdb._client.delete(  # noqa: SLF001
        collection_name=UPLOAD_COLLECTION, filter=f'upload_id == "{upload_id}"'
    )
    vdb._client.flush(collection_name=UPLOAD_COLLECTION)  # noqa: SLF001
    return len(before)


def live_collections() -> list[str]:
    """The collections that exist, which is what a query may name.

    The rag-server refuses a question naming a collection that does not exist — with a 400
    for the whole query, not a skipped collection — so the console asks here first. A
    deployment whose corpus has never indexed and which has no uploads has neither.
    """
    out: list[str] = []
    try:
        vdb, _ = vector_store()
        for collection in (COLLECTION, UPLOAD_COLLECTION):
            if collection and vdb.check_collection_exists(collection):
                out.append(collection)
    except Exception as error:  # noqa: BLE001
        logger.warning("could not list the collections: %s", error)
    return out


# --------------------------------------------------------------------------------------
# The loop and the status endpoint
# --------------------------------------------------------------------------------------


class Service(ThreadingHTTPServer):
    """Threaded, because indexing one upload must not block the status panel.

    `HTTPServer` serves one request at a time, and an upload holds its request for as long
    as extraction takes — 52.6 s measured for a fifteen-page scan through OCR. On a
    single-threaded server that is a minute in which `/status` does not answer, which is a
    readiness probe failing and the pod being restarted mid-upload.
    """

    daemon_threads = True


class Status(BaseHTTPRequestHandler):
    """The console's only way in. No authentication of its own, and that is deliberate.

    This is a ClusterIP Service on no Ingress. The console is the whole of the
    authentication in front of it and it is the only caller — the same arrangement as the
    rag-server, which also answers this namespace without a credential. What a second
    workload in the namespace could reach is the reason the EXTRACTION service, which this
    one calls, does carry a key.
    """

    protocol_version = "HTTP/1.1"

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def route(self) -> str:
        return self.path.split("?")[0].rstrip("/") or "/"

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's own naming
        route = self.route()
        if route in ("/status", "/healthz"):
            with STATE_LOCK:
                payload = dict(STATE)
            if route == "/status":
                payload["uploadedDocuments"] = uploaded_documents()
            self.respond(200, payload)
            return
        if route == "/collections":
            # Which collections a query may name. Cheap: two existence checks, no embedding.
            self.respond(200, {"collections": live_collections()})
            return
        if route == "/documents":
            self.respond(200, {"documents": uploaded_documents()})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        """`POST /documents` — one file, raw, with its name in `X-Document-Name`.

        Raw bytes and a header rather than a multipart body: the console has already parsed
        the browser's multipart form, and parsing it a second time here would mean a
        multipart parser in a `BaseHTTPRequestHandler`, which is a place to put a bug rather
        than a feature.
        """
        if self.route() != "/documents":
            self.send_error(404)
            return
        if not uploads_enabled():
            self.respond(
                503,
                {
                    "error": "this deployment has no extraction service, so it reads "
                    "documents from its gated source only"
                },
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.respond(400, {"error": "Content-Length is required"})
            return
        if length <= 0:
            self.respond(400, {"error": "an empty document has nothing to index"})
            return
        if length > UPLOAD_MAX_BYTES:
            self.respond(
                413,
                {
                    "error": f"{length} bytes; this deployment accepts up to "
                    f"{UPLOAD_MAX_BYTES}"
                },
            )
            return
        try:
            name = safe_name(self.headers.get("X-Document-Name") or "")
        except ValueError as error:
            self.respond(400, {"error": str(error)})
            return

        blob = self.rfile.read(length)
        try:
            result = store_upload(name, blob)
        except ValueError as error:
            self.respond(400, {"error": str(error)})
            return
        except TimeoutError as error:
            self.respond(504, {"error": str(error)})
            return
        except Exception as error:  # noqa: BLE001 — the caller is told, the pod stays up
            logger.exception("indexing the submitted document failed")
            self.respond(502, {"error": str(error)[:400]})
            return
        self.respond(201, result)

    def do_DELETE(self) -> None:  # noqa: N802
        """`DELETE /documents/<id>` — every chunk of one submitted document."""
        route = self.route()
        if not route.startswith("/documents/"):
            self.send_error(404)
            return
        upload_id = route[len("/documents/") :]
        try:
            with INDEX_LOCK:
                removed = remove_upload(upload_id)
        except ValueError as error:
            self.respond(400, {"error": str(error)})
            return
        except Exception as error:  # noqa: BLE001
            logger.exception("removing the submitted document failed")
            self.respond(502, {"error": str(error)[:400]})
            return
        if not removed:
            self.respond(404, {"error": "no such document"})
            return
        self.respond(200, {"id": upload_id, "chunks": removed})

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
            # The same lock an upload takes: one embedder, two cores, and two kinds of work
            # that would otherwise saturate it together.
            with INDEX_LOCK:
                written = index(rows, binding)
        except TimeoutError as error:
            # Said apart from a refusal: the source served the documents and the index did not
            # take them. An operator reading this needs to look at the embedder, not the gate.
            logger.warning("indexing stalled: %s", error)
            publish(verdict="stalled", message=str(error)[:400])
            time.sleep(POLL_SECONDS)
            continue
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
    server = Service(("0.0.0.0", STATUS_PORT), Status)
    threading.Thread(target=server.serve_forever, name="status", daemon=True).start()
    logger.info("status on :%d, polling every %ds", STATUS_PORT, POLL_SECONDS)
    loop()


if __name__ == "__main__":
    main()
