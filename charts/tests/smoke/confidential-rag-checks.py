#!/usr/bin/env python3
"""The questions `confidential-rag.sh` asks of the stack it raised.

Separated from the shell because each one is a claim with a shape — a score that
is not zero, a flag that is set, a table that came back as a grid — and shell is
a poor place to read JSON.

Three groups, in the order a deployment meets them:

1. **A submitted document is extracted, indexed and retrievable.** Through the
   console's own session, because the console is the only door and a check that
   went round it would prove the indexer works and leave the door untried. The
   table in the document has to come back as a grid; the chart has to come back
   located rather than transcribed.

2. **A reranker that refuses the batch is reported.** Reproduced the way it
   happened: ask for more candidates than the reranker's
   `--max-client-batch-size`, which is what 0.1.5 did by accident. The answer
   must still arrive, its scores must be the retriever's own rather than 0.0,
   the citations must say `degraded`, and `/v1/health` must say so too. Every
   one of those four was absent in 0.1.7 and that is why it shipped a reranker
   doing nothing.

3. **A working reranker is not reported as degraded**, which is the half that
   makes the alarm worth having.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

failures: list[str] = []


def check(condition: bool, description: str, detail: str = "") -> bool:
    if condition:
        print(f"  ok    {description}")
        return True
    print(f"  FAIL  {description}")
    if detail:
        print(f"          {detail}")
    failures.append(description)
    return False


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Stop at the 303.

    A successful sign-in answers `303 See Other` with the session cookie on it,
    and `urlopen` follows redirects by default — so the headers that come back
    are the redirected GET's, with no `Set-Cookie` anywhere in them. That is a
    sign-in that looks like a wrong password.
    """

    def redirect_request(self, *_args, **_kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirects)


def request(url: str, *, data=None, headers=None, method=None, timeout=120):
    """Status, body and headers. Headers as the message object, not a dict.

    `dict(response.headers)` loses the case-insensitivity HTTP headers have, and
    a 303 from `urlopen` arrives through `HTTPError` where the casing differs
    from the success path — which made a correct sign-in read as a missing
    cookie.
    """
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as answer:
            return answer.status, answer.read(), answer.headers
    except urllib.error.HTTPError as error:
        # A 3xx reaches here because the opener above refuses to follow it.
        return error.code, error.read(), error.headers


def sign_in(console: str, password: str) -> str:
    body = urllib.parse.urlencode({"password": password}).encode()
    status, payload, headers = request(
        console + "/login",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    cookie = headers.get("Set-Cookie", "")
    if status != 303 or not cookie:
        raise SystemExit(f"  FAIL  the console did not issue a session ({status})")
    # `Secure` is on the real cookie and a browser would refuse to send it over
    # plain http — which is correct, and is why this run talks to the console
    # with the cookie by hand rather than claiming to prove the browser flow.
    # The deployment is served over TLS by the cloud's gateway and nothing else.
    return cookie.split(";")[0]


# A document with the three element kinds in it, written here rather than
# committed as a binary: HTML is one of the formats the extractor takes, and a
# fixture a reader can read is worth more than a PDF they cannot.
#
# It is long on purpose. The defect this run reproduces needs the retriever to
# hand the reranker MORE than `--max-client-batch-size` passages, so the corpus
# has to hold more chunks than that ceiling — a three-paragraph document would
# produce a batch of three, no 413, and a green run that proved nothing. Fifty
# sections is also closer to the thing a tenant submits than three paragraphs.
SECTIONS = 50

PARAGRAPH = (
    "Section {n}. Expenses in category {n} are reimbursed within thirty days of "
    "submission. Receipts are required for anything above fifty euro, and a receipt "
    "in a language other than English needs a one-line translation beside it. "
    "Approval for category {n} sits with the regional finance lead, except where the "
    "claim covers travel booked outside the corporate tool, in which case it sits "
    "with the budget owner named in the cost centre."
)

DOCUMENT = """<!doctype html>
<html><head><title>Acme expenses handbook</title></head><body>
<h1>Acme expenses handbook</h1>
{sections}
<h2>Per-diem rates by region</h2>
<table>
  <thead><tr><th>Region</th><th>Per diem</th><th>Hotel cap</th></tr></thead>
  <tbody>
    <tr><td>EMEA</td><td>65</td><td>180</td></tr>
    <tr><td>Americas</td><td>75</td><td>220</td></tr>
    <tr><td>APAC</td><td>60</td><td>170</td></tr>
  </tbody>
</table>
<figure>
  <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==" alt="">
  <figcaption>Figure 1: expense claims per quarter</figcaption>
</figure>
</body></html>
""".format(
    sections="\n".join(
        f"<p>{PARAGRAPH.format(n=number)}</p>" for number in range(1, SECTIONS + 1)
    )
)


def multipart(name: str, blob: bytes) -> tuple[bytes, str]:
    boundary = "----cragsmoke"
    buffer = io.BytesIO()
    buffer.write(f"--{boundary}\r\n".encode())
    buffer.write(
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
    )
    buffer.write(b"Content-Type: application/octet-stream\r\n\r\n")
    buffer.write(blob)
    buffer.write(f"\r\n--{boundary}--\r\n".encode())
    return buffer.getvalue(), f"multipart/form-data; boundary={boundary}"


def search(rag: str, question: str, collections: list[str], *, top_k: int):
    """`/v1/search` — retrieve, rerank and cite, with no model writing prose."""
    payload = {
        "query": question,
        "collection_names": collections,
        "reranker_top_k": 6,
        "vdb_top_k": top_k,
        "ranking_options": {"ranker": "auto", "score_threshold": 0.0},
    }
    status, body, _ = request(
        rag + "/v1/search",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return status, body


def ranking_entry(rag: str) -> dict:
    status, body, _ = request(rag + "/v1/health?check_dependencies=true")
    if status != 200:
        return {}
    report = json.loads(body)
    for service in report.get("nim") or []:
        if str(service.get("service", "")).lower() == "ranking":
            return service
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--console", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--rag", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--uploads", required=True)
    args = parser.parse_args()

    session = sign_in(args.console, args.password)
    auth = {"Cookie": session}

    # -------------------------------------------------------------------
    print("\n  a deployment with nothing indexed")
    # -------------------------------------------------------------------
    # First, because it is only true before anything is submitted. No dataset is
    # attached in this run and no document has been sent yet, so neither
    # collection exists — and the rag-server answers a question naming a
    # collection that does not exist with `400 … Ensure a collection is created
    # using POST /collection`, which is advice about an endpoint this listing
    # does not publish.
    status, payload, _ = request(
        args.console + "/api/ask",
        data=json.dumps({"question": "what is the per diem for EMEA?"}).encode(),
        headers={**auth, "Content-Type": "application/json"},
        method="POST",
    )
    check(
        status == 200 and b"nothing to search yet" in payload,
        "a question before anything is indexed says so, in words a tenant can act on",
        f"{status}: {payload[:300]}",
    )

    # -------------------------------------------------------------------
    print("\n  a submitted document")
    # -------------------------------------------------------------------
    body, content_type = multipart("acme-expenses-handbook.html", DOCUMENT.encode())
    status, payload, _ = request(
        args.console + "/api/documents",
        data=body,
        headers={**auth, "Content-Type": content_type},
        method="POST",
        timeout=900,
    )
    submitted = json.loads(payload) if payload else {}
    if not check(
        status == 201, "the console accepts a document and the indexer takes it",
        f"{status}: {submitted}",
    ):
        return 1

    counts = submitted.get("elements") or {}
    check(submitted.get("chunks", 0) > 0, f"it is indexed ({submitted['chunks']} chunks)")
    check(counts.get("tables", 0) >= 1, f"the table is extracted as a table ({counts})")
    check(
        counts.get("charts", 0) >= 1,
        f"the figure is located rather than read as text ({counts})",
    )
    check(
        counts.get("chartsIndexed", 0) <= counts.get("charts", 0),
        "and no more charts are indexed than were found",
    )

    status, payload, _ = request(args.console + "/api/documents", headers=auth)
    listed = (json.loads(payload).get("documents") or []) if status == 200 else []
    check(
        any(entry["name"] == "acme-expenses-handbook.html" for entry in listed),
        "the console lists what has been submitted",
        f"{status}: {listed}",
    )

    # Milvus loads a new collection asynchronously; a query straight after the
    # first insert can see an empty segment.
    time.sleep(8)

    status, payload, _ = request(args.console + "/api/status", headers=auth)
    panel = json.loads(payload) if status == 200 else {}
    check(
        (panel.get("corpus") or {}).get("uploads", {}).get("enabled") is True,
        "the status panel says this deployment accepts documents",
    )

    # -------------------------------------------------------------------
    print("\n  the reranker, working")
    # -------------------------------------------------------------------
    status, body = search(
        args.rag, "what is the per diem for EMEA?", [args.uploads], top_k=12
    )
    if not check(status == 200, "a question over the submitted document answers", body[:400]):
        return 1
    answer = json.loads(body)
    citations = answer.get("citations") or answer
    results = citations.get("results") or []
    check(bool(results), f"with citations ({len(results)})")
    report = citations.get("reranker") or {}
    check(
        report.get("degraded") is False,
        f"and the answer says it WAS reranked ({report})",
    )
    check(
        all((result.get("score") or 0) > 0 for result in results),
        "every citation carries a non-zero score",
        str([result.get("score") for result in results]),
    )
    grids = [
        result for result in results
        if "| Region" in (result.get("content") or "")
        or "Per diem" in (result.get("content") or "")
    ]
    check(
        bool(grids),
        "the table comes back as a grid a model can read, not as flattened prose",
        str([(result.get("content") or "")[:80] for result in results]),
    )
    entry = ranking_entry(args.rag)
    check(
        str(entry.get("status")) == "healthy",
        f"and /v1/health reports the reranker healthy ({entry.get('status')})",
    )

    # -------------------------------------------------------------------
    print("\n  the reranker, refusing every batch — the 0.1.5 defect, on purpose")
    # -------------------------------------------------------------------
    # 40 candidates against `--max-client-batch-size 32`. This is not a
    # hypothetical: it is the exact configuration that shipped, and the exact
    # error — `413 batch size 40 > maximum allowed batch size 32` — that reached
    # nothing but the log.
    status, body = search(
        args.rag, "what is the per diem for EMEA?", [args.uploads], top_k=40
    )
    if not check(status == 200, "the question still answers with the reranker refusing", body[:400]):
        return 1
    answer = json.loads(body)
    citations = answer.get("citations") or answer
    results = citations.get("results") or []
    report = citations.get("reranker") or {}

    check(report.get("degraded") is True, f"the answer says it was NOT reranked ({report})")
    check(
        "413" in str(report.get("reason")),
        f"and names the refusal the endpoint gave ({report.get('reason')})",
    )
    check(
        report.get("score_source") == "retriever",
        "and says the scores below are the retriever's own",
    )
    scores = [result.get("score") or 0 for result in results]
    check(
        bool(scores) and all(score > 0 for score in scores),
        "every citation still carries a NON-ZERO score — the defect was zeroes here",
        str(scores),
    )
    check(
        scores == sorted(scores, reverse=True),
        "in the retriever's own descending order",
        str(scores),
    )

    entry = ranking_entry(args.rag)
    check(
        str(entry.get("status")) == "degraded",
        f"/v1/health reports the reranker degraded ({entry.get('status')})",
    )
    check(
        "413" in str(entry.get("message")),
        f"with the reason, on a worker that may not be the one that reranked "
        f"({entry.get('message')})",
    )

    status, payload, _ = request(args.console + "/api/status", headers=auth)
    panel = (json.loads(payload) if status == 200 else {}).get("reranker") or {}
    check(
        panel.get("degraded") is True,
        f"and the console's status panel shows it ({panel})",
    )
    check(
        "413" in str(panel.get("reason")),
        f"with the reason a person can act on ({panel.get('reason')})",
    )

    # -------------------------------------------------------------------
    print("\n  and it recovers")
    # -------------------------------------------------------------------
    status, body = search(
        args.rag, "what is the per diem for APAC?", [args.uploads], top_k=12
    )
    answer = json.loads(body) if status == 200 else {}
    report = (answer.get("citations") or answer).get("reranker") or {}
    check(
        report.get("degraded") is False,
        f"a later answer within the ceiling is reranked again ({report})",
    )
    for _ in range(12):
        entry = ranking_entry(args.rag)
        if str(entry.get("status")) == "healthy":
            break
        time.sleep(2)
    check(
        str(entry.get("status")) == "healthy",
        f"and the health report stops saying degraded ({entry.get('status')})",
    )

    # -------------------------------------------------------------------
    print("\n  removing a document")
    # -------------------------------------------------------------------
    target = next(
        (entry for entry in listed if entry["name"] == "acme-expenses-handbook.html"),
        None,
    )
    if target:
        status, payload, _ = request(
            args.console + f"/api/documents/{target['id']}", headers=auth, method="DELETE"
        )
        check(status == 200, f"the console removes it ({status}: {payload[:200]})")
        status, payload, _ = request(args.console + "/api/documents", headers=auth)
        remaining = (json.loads(payload).get("documents") or []) if status == 200 else []
        check(
            not any(e["id"] == target["id"] for e in remaining),
            "and it is gone from the list",
            str(remaining),
        )

    if failures:
        print(f"\n  {len(failures)} check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
