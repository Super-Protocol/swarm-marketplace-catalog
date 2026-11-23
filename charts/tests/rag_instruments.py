#!/usr/bin/env python3
"""The instruments this listing adds, exercised against the code the cloud signs.

    charts/tests/rag_instruments.py

Every other check in this directory reads a render. These two cannot be: the
reporting that 0.1.8 is about is PYTHON, rendered into `confidential-rag`'s
`-app` ConfigMap, and a render diff would say the file is present and nothing
about whether it reports anything. So this pulls `console.py` and
`corpus_sync.py` out of the rendered ConfigMap — not out of `files/`, so that a
template that stopped shipping one of them would fail here — and calls them.

**Why this file exists at all.** Confidential RAG's reranker did no work on any
query for two versions. 0.1.6 found the cause (`topK` above the server's batch
ceiling) and 0.1.7 found the next one (thread pools sized from the host), and
*both* were found by deploying the listing, reading a citation and noticing the
score was 0.0 — because `TEIRerank`'s fallback was correct and silent. The fix
for the third occurrence is not another number: it is that a degraded reranker
says so. That makes the reporting the deliverable, and a deliverable with no
test is how this class of defect survives a review (SUP-889).

The second half is the same argument for document submission. "Charts are cited
as images" is a sentence in the listing's copy; in the code it is the branch
that refuses to index a picture's pixels as if they were its data. A listing
whose copy and whose code disagreed about that would be making a promise about
a tenant's documents that nothing keeps (SUP-890).

What this does NOT cover, said plainly: the HTTP handlers, the session check and
the calls to Milvus and to the extraction service. Those need a cluster or a
stack, and `charts/tests/rag_console.py` holds the render-side properties of the
door. What is here is every pure function in which a wrong answer would be a
wrong statement to a tenant.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts/confidential-rag"
VALUES = ROOT / "charts/tests/cases/rag-stack.yaml"

failures: list[str] = []


def check(condition: bool, description: str) -> None:
    if condition:
        print(f"  ok    {description}")
    else:
        print(f"  FAIL  {description}")
        failures.append(description)


def rendered_files() -> dict[str, str]:
    """The application code as the ConfigMap carries it.

    Read from the render rather than from `files/` on purpose: what the cloud
    digests and signs is the ConfigMap, and a template that dropped one of these
    keys would leave the pod running the image's own entrypoint instead.
    """
    out = subprocess.run(
        ["helm", "template", "rag", str(CHART), "--namespace", "confidential-rag",
         "--skip-tests", "--values", str(VALUES)],
        capture_output=True, text=True, check=True,
    ).stdout
    for document in yaml.safe_load_all(out):
        if (
            document
            and document.get("kind") == "ConfigMap"
            and document["metadata"]["name"].endswith("-app")
        ):
            return document["data"]
    raise SystemExit("  FAIL  no -app ConfigMap in the render")


def load(name: str, source: str, stubs: dict[str, types.ModuleType] | None = None):
    """Import one rendered file, with whatever the image provides stubbed out.

    `corpus_sync.py` imports the retriever's own `nvidia_rag` package — which is
    the point of it, and is in the image rather than here. The stubs are only
    deep enough to let the module import; every function this file calls is pure.
    """
    for module_name, module in (stubs or {}).items():
        sys.modules.setdefault(module_name, module)
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / f"{name}.py"
        path.write_text(source)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


def console_stubs() -> dict[str, types.ModuleType]:
    """What the rag-server image provides and this directory does not.

    `console.py` runs inside that image, where fastapi and httpx already are.
    Stubbing them is what lets the panel's logic be exercised here without
    installing a web framework to call three pure functions — and the stubs are
    deliberately thin: anything that needed more of fastapi than a decorator
    would be a handler, and handlers are not what this file claims to cover.
    """

    def passthrough(*_args, **_kwargs):
        def decorate(function):
            return function

        return decorate

    class App:
        get = post = delete = put = staticmethod(passthrough)

        def __init__(self, *_args, **_kwargs):
            pass

    def module(name: str, **attributes) -> types.ModuleType:
        made = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(made, key, value)
        return made

    def response(*_args, **_kwargs):
        return None

    class Timeout:
        def __init__(self, *_args, **_kwargs):
            pass

    return {
        "httpx": module(
            "httpx",
            AsyncClient=object,
            Timeout=Timeout,
            TimeoutException=TimeoutError,
        ),
        "fastapi": module(
            "fastapi",
            FastAPI=App,
            File=lambda *a, **k: None,
            Form=lambda *a, **k: "",
            Request=object,
            Response=object,
            UploadFile=object,
        ),
        "fastapi.responses": module(
            "fastapi.responses",
            HTMLResponse=response,
            JSONResponse=response,
            RedirectResponse=response,
            StreamingResponse=response,
        ),
    }


def nvidia_rag_stubs() -> dict[str, types.ModuleType]:
    class Document:
        def __init__(self, page_content="", metadata=None):
            self.page_content = page_content
            self.metadata = metadata or {}

    def module(name: str, **attributes) -> types.ModuleType:
        made = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(made, key, value)
        return made

    return {
        "requests": module("requests", post=None, Session=object),
        "langchain_core": module("langchain_core"),
        "langchain_core.documents": module("langchain_core.documents", Document=Document),
        "nvidia_rag": module("nvidia_rag"),
        "nvidia_rag.utils": module("nvidia_rag.utils"),
        "nvidia_rag.utils.configuration": module(
            "nvidia_rag.utils.configuration", NvidiaRAGConfig=object
        ),
        "nvidia_rag.utils.embedding": module(
            "nvidia_rag.utils.embedding", get_embedding_model=lambda **_: None
        ),
        "nvidia_rag.utils.vdb": module("nvidia_rag.utils.vdb"),
        "nvidia_rag.utils.vdb.milvus": module("nvidia_rag.utils.vdb.milvus"),
        "nvidia_rag.utils.vdb.milvus.milvus_vdb": module(
            "nvidia_rag.utils.vdb.milvus.milvus_vdb", MilvusVDB=object
        ),
    }


# ---------------------------------------------------------------------------
# The reranker's verdict, as the console's status panel computes it
# ---------------------------------------------------------------------------


def check_the_panel(console) -> None:
    healthy = {
        "nim": [
            {"service": "Ranking", "model": "bge-reranker-v2-m3", "status": "healthy"},
            {"service": "Embeddings", "status": "healthy"},
        ]
    }
    refused = {
        "nim": [
            {
                "service": "Ranking",
                "model": "bge-reranker-v2-m3",
                "status": "degraded",
                "message": 'reranking is degraded: HTTP 413 from '
                           'http://confidential-rag-reranker/rerank: '
                           '{"error":"batch size 40 > maximum allowed batch size 32"}',
            }
        ]
    }

    console.LAST_RERANKING.clear()
    panel = console.reranker_panel(healthy)
    check(panel["degraded"] is False, "a working reranker is not reported degraded")

    panel = console.reranker_panel(refused)
    check(
        panel["degraded"] is True and "batch size 32" in panel["reason"],
        "a reranker refusing the batch is degraded, with the endpoint's own message",
    )
    check(
        panel["source"] == "health",
        "and the panel says the health report is where that came from",
    )

    # The case the multi-worker pipeline makes real: the worker answering the
    # health request has reranked nothing, so it reports `healthy` — correctly —
    # while the query that was actually served fell back. Letting that overwrite
    # the answer's own verdict is precisely how this stayed invisible.
    console.LAST_RERANKING.clear()
    console.LAST_RERANKING.update(
        degraded=True, reason="HTTP 413 from the reranker", at="2026-11-23T02:00:00Z"
    )
    panel = console.reranker_panel(healthy)
    check(
        panel["degraded"] is True and panel["source"] == "answer",
        "a degradation the last ANSWER hit is reported even when the probed worker is clean",
    )

    console.LAST_RERANKING.clear()
    console.LAST_RERANKING.update(degraded=False, reason="")
    panel = console.reranker_panel(healthy)
    check(
        panel["degraded"] is False,
        "and a recovery clears it rather than sticking for the life of the pod",
    )

    panel = console.reranker_panel({"error": "unreachable"})
    check(
        panel["degraded"] is False and panel["source"] == "none",
        "an unreachable pipeline is not a degraded reranker; it is an unreachable pipeline",
    )


def check_the_watcher(console) -> None:
    """The verdict is read off the stream the console is relaying, as it passes."""
    console.LAST_RERANKING.clear()
    watcher = console.reranking_watcher()
    next(watcher)
    event = (
        b'data: {"choices":[{"delta":{"content":"The handbook says"}}],'
        b'"citations":{"total_results":1,"results":[],'
        b'"reranker":{"degraded":true,"reason":"HTTP 413 from the reranker",'
        b'"score_source":"retriever"}}}\n\n'
    )
    # Split mid-event, because that is what a stream does.
    watcher.send(event[:60])
    watcher.send(event[60:])
    check(
        console.LAST_RERANKING.get("degraded") is True,
        "a degraded answer's own report is read out of the relayed stream",
    )
    check(
        console.LAST_RERANKING.get("scoreSource") == "retriever",
        "including which component the citation scores came from",
    )

    console.LAST_RERANKING.clear()
    watcher = console.reranking_watcher()
    next(watcher)
    watcher.send(b"data: not json at all\n\ndata: [DONE]\n\n")
    check(
        not console.LAST_RERANKING,
        "a stream carrying no report leaves the panel saying nothing rather than guessing",
    )


# ---------------------------------------------------------------------------
# What a submitted document becomes
# ---------------------------------------------------------------------------

TABLE_ELEMENT = {
    "content": "| region | revenue |\n| --- | --- |\n| EMEA | 4.2 |",
    "content_metadata": {"type": "structured", "subtype": "table", "page_number": 4},
    "table_metadata": {
        "table_content": "| region | revenue |\n| --- | --- |\n| EMEA | 4.2 |",
        "table_location": [72.0, 108.5, 523.25, 240.0],
        "table_location_max_dimensions": [612.0, 792.0],
        "caption": "Table 2: revenue by region",
    },
}
CHART_ELEMENT = {
    "content": "",
    "content_metadata": {"type": "image", "page_number": 2},
    "image_metadata": {
        "caption": "Figure 1: revenue over time",
        "image_location": [10.0, 20.0, 300.0, 400.0],
        "image_location_max_dimensions": [612.0, 792.0],
    },
}
UNCAPTIONED_CHART = {
    "content": "",
    "content_metadata": {"type": "image", "page_number": 3},
    "image_metadata": {"image_location": [1.0, 2.0, 3.0, 4.0]},
}
TEXT_ELEMENT = {
    "content": "Expenses are reimbursed within thirty days of submission.",
    "content_metadata": {"type": "text", "page_number": 1},
}


def check_the_elements(sync) -> None:
    documents, counts = element_documents(sync)

    check(
        counts == {"text": 1, "tables": 1, "charts": 2, "chartsIndexed": 1, "chunks": 3},
        "what the extractor found is counted by kind, captioned charts apart from the rest",
    )

    tables = [d for d in documents
              if d.metadata["content_metadata"]["element_type"] == "structured"]
    check(len(tables) == 1, "a table becomes one chunk while it fits the embedder's window")
    check(
        "| region | revenue |" in tables[0].page_content,
        "and it is indexed as its grid, not flattened into prose",
    )
    check(
        tables[0].metadata["table_metadata"]["table_location"] == [72.0, 108.5, 523.25, 240.0],
        "a table carries table_metadata.table_location — the field a citation can read",
    )
    check(
        tables[0].metadata["content_metadata"]["searchable_content"] is True,
        "and its content is searchable, which is the whole reason the component exists",
    )

    images = [d for d in documents
              if d.metadata["content_metadata"]["element_type"] == "image"]
    check(
        len(images) == 1 and images[0].page_content == "Figure 1: revenue over time",
        "a captioned chart is indexed by its CAPTION and nothing else",
    )
    check(
        images[0].metadata["content_metadata"]["searchable_content"] is False,
        "marked not searchable: this stack reads no chart data, and the copy says so",
    )
    check(
        images[0].metadata["image_metadata"]["image_location"] == [10.0, 20.0, 300.0, 400.0],
        "located, so the citation points at where on the page the chart is",
    )
    check(
        counts["charts"] == 2 and counts["chartsIndexed"] == 1,
        "an uncaptioned chart is counted and not indexed — no chunk claims to hold its data",
    )

    keys = {frozenset(d.metadata) for d in documents}
    check(
        len(keys) == 1,
        "every chunk carries the same metadata keys; langchain_milvus builds the "
        "collection's fields from the first batch alone",
    )
    check(
        all(d.metadata["content_metadata"]["type"] == "text" for d in documents),
        "and every chunk is cited down the branch that has its content in hand",
    )
    check(
        {d.metadata["upload_id"] for d in documents} == {"deadbeef"}
        and {d.metadata["document_name"] for d in documents} == {"handbook.pdf"},
        "the document's identity is on every chunk, which is what list and remove filter on",
    )


def element_documents(sync):
    return sync.element_documents(
        [TEXT_ELEMENT, TABLE_ELEMENT, CHART_ELEMENT, UNCAPTIONED_CHART],
        "deadbeef",
        "handbook.pdf",
    )


def check_table_splitting(sync) -> None:
    rows = "\n".join(f"| region {n} | {n * 1.5} | a long description of the row |"
                     for n in range(200))
    markdown = f"| region | revenue | notes |\n| --- | --- | --- |\n{rows}"
    pieces = sync.table_chunks(markdown)
    check(len(pieces) > 1, "a table larger than the chunk width is split")
    check(
        all(piece.startswith("| region | revenue | notes |") for piece in pieces),
        "and every piece repeats the header: a grid of numbers with no column names "
        "embeds, retrieves and answers wrongly rather than failing",
    )
    check(
        all(len(piece) <= sync.CHUNK_CHARS + 200 for piece in pieces),
        "each piece stays within the chunk width the embedder was configured for",
    )
    check(
        sync.table_chunks("| a |\n| --- |") == ["| a |\n| --- |"],
        "a table that fits is one chunk",
    )


def check_names(sync) -> None:
    check(sync.safe_name("notes.pdf") == "notes.pdf", "an ordinary name is kept")
    check(
        sync.safe_name("../../etc/passwd") == "passwd",
        "a path is reduced to its basename before it reaches a field or a log",
    )
    check(
        sync.safe_name(r"C:\\Users\\me\\report.docx") == "report.docx",
        "including a Windows path, which is what a browser on Windows sends",
    )
    check('"' not in sync.safe_name('a"b.pdf'), "a quote cannot reach a Milvus filter")
    for refused in ("", "   ", ".", ".."):
        try:
            sync.safe_name(refused)
            check(False, f"{refused!r} is refused rather than stored as something")
            break
        except ValueError:
            continue
    else:
        check(True, "a name with nothing in it is refused rather than renamed silently")


def check_document_names(console, sync) -> None:
    """A submitted name survives the console, the header and the indexer intact.

    This is the one check in this file that stands for a DELETION. 0.1.8 sent the name
    as `name.encode("ascii", "replace")`, so `Счёт.pdf` and `План.pdf` — equal length,
    both Cyrillic, both entirely ordinary for these users — arrived as `????.pdf`. The
    identifier is the name's hash and a document of the same identifier is REPLACED, so
    submitting the second deleted every chunk of the first, inside an enclave where the
    submitted copy is the only copy (SUP-895).

    The whole path is exercised, not one end of it: what the console puts in the header,
    what the indexer makes of the header, and what identity it derives. A fix to either
    half alone leaves the collision in place.
    """
    first, second = "Счёт.pdf", "План.pdf"
    check(
        len(first) == len(second),
        "the two names in this check are the same length — a length check would not "
        "have caught the collision either",
    )

    def submitted(name: str) -> tuple[str, str]:
        received = sync.received_name(console.document_name_header(name))
        return received, sync.upload_identifier(received)

    first_name, first_id = submitted(first)
    second_name, second_id = submitted(second)

    check(
        first_name == first and second_name == second,
        f"a Cyrillic name reaches the index as itself ({first_name!r}, {second_name!r})",
    )
    check(
        first_id != second_id,
        "two different Cyrillic names of equal length are two documents, not one",
    )
    check(
        submitted(first)[1] == first_id,
        "and the same name is the same document, so a resubmission replaces rather "
        "than duplicates",
    )

    # The header is what travels, and a header is latin-1 on the wire: a name that is
    # not ASCII after encoding would be refused by the HTTP client or mangled by it.
    for name in (first, second, "rapport trimestriel – T4.pdf", "台帳.xlsx"):
        header = console.document_name_header(name)
        check(
            header.isascii() and sync.received_name(header) == name,
            f"{name!r} crosses the namespace as ASCII and arrives as itself",
        )

    check(
        sync.received_name(console.document_name_header("100%_done.pdf"))
        == "100%_done.pdf",
        "a literal percent survives the encoding rather than eating the next two "
        "characters",
    )
    # `safe_name` still does its own work, on the real name instead of on question marks.
    check(
        sync.received_name(console.document_name_header("../../etc/Счёт.pdf"))
        == "Счёт.pdf",
        "and the path is still dropped after the decode, not before it",
    )
    check(
        '"' not in sync.received_name(console.document_name_header('Счёт"1.pdf')),
        "and a quote still cannot reach a Milvus filter",
    )
    for refused in ("", "%2E", "%2E%2E", "%20%20"):
        try:
            sync.received_name(refused)
            check(False, f"{refused!r} is refused rather than stored as something")
            break
        except ValueError:
            continue
    else:
        check(True, "a header that decodes to nothing usable is refused, not renamed")


def check_the_replacement_is_said(console) -> None:
    """A replacement is reported. The count was in the answer and nothing rendered it.

    `replaced` has been in the indexer's response since 0.1.8 and did not appear in the
    console at all, so a deliberate resubmission and the silent deletion of somebody
    else's document looked identical: one success line about the document just indexed.
    """
    ui = (ROOT / "charts/confidential-rag/files/index.html").read_text()
    check("body.replaced" in ui, "the console reads `replaced` out of the indexer's answer")
    check(
        "It replaced the" in ui,
        "and says so in the upload note when it is non-zero",
    )


def check_the_indexer_authenticates(sync) -> None:
    """Every route but `/healthz` needs the key, and the comparison is constant-time.

    `POST /documents` and `DELETE /documents/<id>` WRITE the collection whose answers are
    cited beside the gated corpus. They answered anything in the namespace that could
    reach port 8085 (SUP-895). The handler needs a socket, so what is checked here is the
    shape: the guard exists, every write route calls it, and `/healthz` does not.
    """
    source = (ROOT / "charts/confidential-rag/files/corpus_sync.py").read_text()
    check(
        "hmac.compare_digest" in source,
        "the token comparison is constant-time, not `==` on a secret",
    )
    check(
        source.count("if not self.admitted():") == 3,
        f"all three handlers consult the guard (found "
        f"{source.count('if not self.admitted():')})",
    )
    check(
        "if not CORPUS_API_KEY:\n            self.close_connection = True" in source,
        "and it fails CLOSED: no key configured means no route answers",
    )
    helpers = (ROOT / "charts/confidential-rag/templates/_helpers.tpl").read_text()
    check(
        "corpus.apiKey is required" in helpers,
        "`crag.validate` refuses to render an indexer with no key at all",
    )


def check_identifiers(sync) -> None:
    """The identifier is interpolated into a Milvus filter, so it is checked."""
    for hostile in ('x" or upload_id != "', "../x", "a b", "x" * 65, ""):
        try:
            sync.remove_upload(hostile)
            check(False, f"{hostile!r} is refused before it reaches a filter expression")
            return
        except ValueError:
            continue
        except Exception:
            # Anything else means it got past the check and tried to talk to
            # Milvus, which is exactly what must not happen.
            check(False, f"{hostile!r} reached the vector store")
            return
    check(True, "an identifier this service did not issue never reaches a filter expression")


def main() -> int:
    files = rendered_files()
    for name in ("console.py", "corpus_sync.py", "index.html"):
        check(name in files, f"the signed ConfigMap carries {name}")
    if failures:
        return 1

    # The console refuses to start without these rather than serve an open door,
    # which is a property `rag_console.py` states and this file has to satisfy.
    os.environ.setdefault("CONSOLE_PASSWORD", "ACCESS-PASSWORD-FOR-THE-TEST")
    os.environ.setdefault("CONSOLE_SESSION_KEY", "SESSION-KEY-FOR-THE-TEST-0123456789")
    os.environ.setdefault("UPLOAD_COLLECTION_NAME", "documents_uploads")
    os.environ.setdefault("CORPUS_SYNC_URL", "http://confidential-rag-corpus:8085")
    os.environ.setdefault("UPLOAD_COLLECTION_NAME", "documents_uploads")
    os.environ.setdefault("INGEST_URL", "http://swarm-doc-ingest:8080")

    console = load("crag_console", files["console.py"], console_stubs())
    sync = load("crag_corpus_sync", files["corpus_sync.py"], nvidia_rag_stubs())

    check_the_panel(console)
    check_the_watcher(console)
    check_the_elements(sync)
    check_table_splitting(sync)
    check_names(sync)
    check_document_names(console, sync)
    check_the_replacement_is_said(console)
    check_the_indexer_authenticates(sync)
    check_identifiers(sync)

    # The listing's copy and this code have to agree about charts, because the
    # copy is the promise and the code is what keeps it.
    listing = (ROOT / "apps/confidential-rag/app.yaml").read_text()
    check(
        "CITED AS IMAGES" in listing and "NOT searchable" in listing,
        "the listing says charts are cited as images and their data is not searchable",
    )
    check(
        "charts are cited as images" in (ROOT / "charts/confidential-rag/files/index.html")
        .read_text().replace("\n", " ").replace("  ", " ")
        or "cited as images" in (ROOT / "charts/confidential-rag/files/index.html").read_text(),
        "and so does the console a person actually reads",
    )

    if failures:
        print(f"  {len(failures)} instrument check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
