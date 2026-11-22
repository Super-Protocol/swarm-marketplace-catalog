#!/usr/bin/env python3
"""The retriever's top-k has to fit what the reranker will accept, and nothing else checks it.

    charts/tests/rag_reranker_budget.py

Two numbers in two different charts have to agree:

* `charts/confidential-rag`'s `server.topK` is how many candidates the retriever hands the
  reranker per question;
* `charts/text-embeddings-server`'s reranker preset sets `--max-client-batch-size`, and Text
  Embeddings Inference refuses a larger batch outright —
  `413 {"error":"batch size 40 > maximum allowed batch size 32","error_type":"Validation"}`.

Nothing checked that they agreed, and on SUP-840 they did not: `topK` was 40 against a ceiling of
32, so **every** rerank was refused in the first millisecond. `TEIRerank` caught it and fell back
to the retriever's own order, which is what it is designed to do — so the answers stayed correct
and the only symptom anywhere was citations whose `relevance_score` was absent and therefore
displayed as 0.0. The listing advertised a reranker, attested it, and budgeted 6Gi and two cores
for it, and it did no work on any query.

`crag.validate` now refuses that render. This is the other half: it holds the ceiling the chart
mirrors equal to the one the server is actually started with, because a mirrored constant that
drifts puts the guard back to sleep.

It also records the measured time budget, which is the second ceiling over `topK` and not one a
render can check: on the two cores the reranker gets, for chunks of `corpus.chunkChars`, scoring
8 passages took 19.5 s, 12 took 30.1 s and 16 took 40.2 s, against the adapter's 60 s timeout.

That budget holds only while the server's thread pools are pinned to the cores the limit grants.
Unpinned they are sized from the host, and on a 128-core GPU node the same two cores took over
60 s for twelve passages — the timeout, so the scores were dropped and the citations read 0.0 on
a live deployment (SUP-840, 2026-11-22). The pinning is therefore part of what makes the number
below true, and this test holds the rendered environment to it rather than trusting the chart.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RERANKER_PRESET = "bge-reranker-v2-m3"
# Seconds per passage on two PINNED cores, from (40.2 - 19.5) / (16 - 8). Reports the margin.
SECONDS_PER_PASSAGE = 2.6
ADAPTER_TIMEOUT_SECONDS = 60.0
# The pools the server starts, which it sizes from the host unless these say otherwise.
THREAD_VARIABLES = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS")


def pinning_problems(preset: dict) -> list[str]:
    """Hold the rendered reranker to thread pools the size of its CPU limit."""
    rendered = subprocess.run(
        ["helm", "template", "rr", str(ROOT / "charts/text-embeddings-server"),
         "--skip-tests", "--set", "apiKey=k", "--set", "name=rr",
         "--set", f"model.preset={RERANKER_PRESET}"],
        capture_output=True, text=True, check=True,
    ).stdout
    deployments = [doc for doc in yaml.safe_load_all(rendered)
                   if doc and doc["kind"] == "Deployment"]
    if len(deployments) != 1:
        return [f"{len(deployments)} Deployments rendered; expected the server's one"]

    servers = [container for container in deployments[0]["spec"]["template"]["spec"]["containers"]
               if container["name"] == "server"]
    if len(servers) != 1:
        return ["no container named `server` in the rendered Deployment"]

    cpu = str(preset["sizing"]["resources"]["cpu"])
    environment = {entry["name"]: entry.get("value")
                   for entry in servers[0].get("env", []) if "value" in entry}
    problems = []
    for variable in THREAD_VARIABLES:
        if environment.get(variable) != cpu:
            problems.append(
                f"{variable} renders as {environment.get(variable)!r} and the limit is {cpu} "
                "core(s) — unpinned pools are sized from the host, which is what made twelve "
                "passages take over 60 s in the cluster and dropped every relevance score."
            )
    return problems


def main() -> int:
    rag = yaml.safe_load((ROOT / "charts/confidential-rag/values.yaml").read_text())
    tes = yaml.safe_load((ROOT / "charts/text-embeddings-server/values.yaml").read_text())

    preset = tes["model"]["presets"][RERANKER_PRESET]
    served = int(preset["sizing"]["maxClientBatchSize"])
    mirrored = int(rag["reranker"]["maxClientBatchSize"])
    top_k = int(rag["server"]["topK"])

    problems: list[str] = []

    if mirrored != served:
        problems.append(
            f"confidential-rag mirrors a reranker batch ceiling of {mirrored}, but the "
            f"{RERANKER_PRESET} preset starts the server with --max-client-batch-size {served}. "
            "The chart's guard is computed from the mirror, so a stale mirror is a guard that "
            "passes a render the server will refuse."
        )

    if top_k > served:
        problems.append(
            f"server.topK is {top_k} and the reranker accepts {served} — every rerank would be "
            f"refused with 413 and silently fall back to the retriever's order."
        )

    budget = top_k * SECONDS_PER_PASSAGE
    if budget > ADAPTER_TIMEOUT_SECONDS:
        problems.append(
            f"server.topK is {top_k}, which is about {budget:.0f}s of reranking on two cores "
            f"against the adapter's {ADAPTER_TIMEOUT_SECONDS:.0f}s timeout. Measured: 8 -> 19.5s, "
            "12 -> 30.1s, 16 -> 40.2s."
        )

    problems.extend(pinning_problems(preset))

    if problems:
        print("  FAIL  the retriever asks for more than the reranker will do")
        for problem in problems:
            print(f"          {problem}")
        return 1

    margin = ADAPTER_TIMEOUT_SECONDS - budget
    print(
        f"  ok    topK {top_k} is within the reranker's batch ceiling {served} "
        f"(mirrored correctly) and ~{budget:.0f}s of the {ADAPTER_TIMEOUT_SECONDS:.0f}s "
        f"adapter budget, {margin:.0f}s spare"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
