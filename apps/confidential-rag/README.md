# Confidential RAG

> Based on the open-source [NVIDIA RAG reference architecture](https://github.com/NVIDIA-AI-Blueprints/rag),
> Apache-2.0, forked at release `v2.6.2` (commit `f20716d7`) and modified by Super Protocol — see
> [`MODIFICATIONS.md`](https://github.com/Super-Protocol/confidential-rag/blob/confidential/2.6.2/MODIFICATIONS.md)
> in [our fork](https://github.com/Super-Protocol/confidential-rag). NVIDIA archived the upstream
> repository on 2026-11-05 and it no longer moves; this fork is maintained by Super Protocol.
> NVIDIA is a trademark of NVIDIA Corporation. This listing is not produced, endorsed or supported
> by NVIDIA.

Semantic search and question answering over an organisation's own documents, inside a confidential
cluster space. Every deployment publishes a signed snapshot of exactly which code, which embedder
and which model are touching the corpus, and the data owner can withdraw access the moment that
snapshot stops matching the one they approved.

## What was substituted, and what that cost

The reference architecture is Apache-2.0. Three of the components it is wired to are not:

| Upstream | Here | Why |
|---|---|---|
| NeMo Retriever embedding NIM | BGE Large EN v1.5 or Snowflake Arctic Embed L v2.0, on Text Embeddings Inference | A NIM needs an NVIDIA AI Enterprise entitlement for production and a per-tenant NGC credential to pull — a credential a listing cannot carry. |
| NeMo Retriever reranking NIM | BGE Reranker v2 M3, on the same server | As above. The API differs too: TEI speaks `POST /rerank`, the NIM `POST /v1/ranking`. |
| Generation NIM | vLLM, through the open-inference component | As above, plus: an opaque container in the middle of a system whose claim is that you can verify what ran. |
| Elasticsearch (the compose default) | Milvus | Elasticsearch's server is under the Elastic License. Milvus is Apache-2.0 and is the blueprint's own alternative. |
| Redis Stack | Valkey | Redis Stack is under RSALv2/SSPL. Valkey is BSD-3-Clause. |
| `nv-ingest` and four extraction NIMs — Nemotron Page Elements, Table Structure, Graphic Elements, Nemotron OCR | [`swarm-doc-ingest`](../../charts/swarm-doc-ingest/): Docling, TableFormer `accurate` and RapidOCR, CPU-only | As above, four times over. Chosen by measurement rather than by reading model cards (SUP-874): mean table F1 **0.844** against Unstructured's 0.332 on the same ten documents, and **0.935** against 0.238 on the reference architecture's own three. The control row is the argument for having the stage at all — a text extractor with no layout or table model reads born-digital prose at a character error rate of 0.0000 and recovers **0 tables out of 19**. |

**What we give up:** NVIDIA's tuned serving stack and its throughput, NVIDIA's enterprise support
and release cadence, and chart transcription (see "What this version does not do"). **What that
buys:** no NVIDIA AI Enterprise licence — about $4,500 per GPU per year — no NGC credential for a
tenant to hold, and no closed container inside the attestation.

**The comparison against NIM is not measured, and this says so rather than guessing.** Nobody on
this project has an NGC credential, so no NIM deployment has been run on this hardware. The
regression against NIM's TensorRT-LLM engine is an open number; what is measured is in
[`docs/open-inference-component.md`](../../docs/open-inference-component.md).

**What a NIM brought with it that a chart now has to supply.** A NIM ships its own serving
configuration; an open server ships defaults meant for a machine, not for a container. Text
Embeddings Inference sizes its thread pools from the host's core count, which a CPU limit does not
change — so on a GPU node with 128 cores the reranker started 128 threads inside its two-core
quota and spent 60 s on a question it does 12 passages of in 15 s. The retriever's adapter timed
out, fell back to the retriever's own order, and every citation came back reading `0.0`: a
component this listing advertises, attests and budgets for, doing nothing, with correct answers as
the only visible output. `text-embeddings-server` 0.1.5 pins the pools to the cores the limit
grants, and `charts/tests/rag_reranker_budget.py` holds the render to it. Worth saying plainly:
this class of defect is invisible in a render, in a probe and in an answer — it is visible only in
a number nobody looks at.

**Which is why, from 0.1.8, the deployment says it itself.** Two versions of this listing were
spent finding two separate reasons the reranker refused every request, and both were found by
deploying the thing and noticing a citation score was `0.0`. The fallback that hid them — keep the
retriever's own order when the reranker is lost — is correct and stays; what was wrong was that it
told nobody. Now a fallback keeps the retriever's own similarity as the score, the answer's
citations carry `reranker: {degraded, reason, score_source}`, `/v1/health` reports the reranker
`degraded` with the endpoint's own message instead of the `healthy` its probe sees, and the
console's panel shows that beside the reranker's name. A third occurrence is a line on a status
panel rather than a version of this listing.

A health probe answering 200 is not evidence that a component is working. That is the general
lesson and it generalises past this listing: the question to ask of a deployment is not "is
everything up" but "what here would notice a component doing nothing".

## The gatekeeper, and the one thing it cannot protect

A dataset whose owner does not hand out credentials is served through the marketplace
**gatekeeper**: this deployment is given a local address, every request through it is admitted
against the evidence the deployment published, and the publisher's own host, user and password are
never part of anything rendered into the namespace. Change the deployment and the approval no
longer holds; reads stop.

**The gatekeeper speaks ClickHouse over HTTP and nothing else.** It injects
`x-clickhouse-user` / `x-clickhouse-key` into every request it forwards and rewrites the database
the caller named. So this listing gates a **ClickHouse-backed document source** — one row per
document — and a corpus held in **object storage cannot be gated today**, whatever a dataset's
`access.mode` says. An S3 or Azure corpus has to be loaded into a ClickHouse table to be protected
this way. That is a platform limit, not a property of this listing; registering an object-storage
document contract is SUP-881, and until the platform's own interface registry carries it, naming it
in a slot would make this listing unpublishable everywhere.

The console shows the gate's verdict on the last read — `admitted`, `refused` with the gate's own
message, or `pending` — so a withdrawn approval is visible rather than being a slow decline in
answer quality.

## What you are agreeing to

The software stack is Apache-2.0. The **weights are licensed separately**, and the option label
that chooses one names its licence. Both the console and the deployment's signed evidence carry it,
so it is not something a tenant has to have read this page to know.

| Weights | Licence | What bites |
|---|---|---|
| Llama 3.2 3B Instruct *(default)* | [Llama 3.2 Community License](https://www.llama.com/llama3_2/license/) | Commercial use granted below 700 million monthly active users; a product built on it displays "Built with Llama"; Meta's Acceptable Use Policy applies. |
| Qwen3-Coder 30B A3B FP8 | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) | Nothing beyond Apache-2.0. Code-tuned: it is the strongest of the measured set and it was trained for code, which is worth knowing when choosing it for prose. |
| BGE Large EN v1.5 *(default embedder)* | MIT | Nothing. |
| Snowflake Arctic Embed L v2.0 | Apache-2.0 | Nothing. |
| BGE Reranker v2 M3 | Apache-2.0 | Nothing. |

Every container image is pinned by digest, pulls anonymously, and needs no vendor account. The
pipeline's own image carries `LICENSE`, upstream's third-party licence list and `MODIFICATIONS.md`
at `/licenses`.

## Your documents: two ways in

**A gated table.** A ClickHouse document source the data owner approved, read through the
gatekeeper, re-read on a timer and re-indexed when its row count or its high-water mark moves.
This is the path the gatekeeper story runs through and the one the digest is declared for.

**A file you submit in the console.** PDF, DOCX, PPTX, XLSX, HTML, Markdown, plain text, and PNG /
JPEG / TIFF / BMP scans, extracted by `swarm-doc-ingest` inside the deployment — no document leaves
the cluster space and nothing is fetched while one is being read, because the extraction models are
baked into the image with the model hub switched off. Submit, list and remove are in the console,
which is the collection and document management upstream's `ingestor-server` would have provided.

Uploaded documents are indexed into a **collection of their own**, and that is load-bearing rather
than tidy: the gated corpus is dropped and rewritten whenever it moves — a document its owner
deleted has to leave the index — so one collection for both would delete every submitted file on
the next refresh of the corpus, with nothing in any log or panel saying it had happened. The chart
refuses a render where the two collection names are equal. A question searches both.

### What is searchable, and what is only located

| | |
|---|---|
| Text | extracted and searchable |
| **Tables** | **extracted as structure and searchable** — indexed as the grid, split on row boundaries with the header repeated, never flattened into prose. This is the reason the extraction stage exists rather than a text extractor |
| **Charts and images** | **located, not transcribed.** A chart is cited as an image at a position on a page, by its caption; its contents are **not read and not searchable**. A chart with no caption is counted in the console's report of what was extracted and is not indexed at all — a chunk claiming to hold numbers nothing here has read would be worse than its absence |
| Scanned pages | OCR'd (RapidOCR, character error rate 0.0000 on the bake-off corpus), about 3.5 s a page |

Transcribing a chart is what NVIDIA's Graphic Elements NIM does, and there is no open equivalent
that runs on CPU; doing it properly needs a vision-language model on a GPU, and the card in this
deployment belongs to the model writing your answers.

**One document is extracted at a time** and the rest queue. That is the memory measurement rather
than an unfinished feature: one document peaks at 2.34 GiB against a 4Gi limit sized from it, so
two in flight would be OOMKilled mid-request — which reads to a tenant as a broken service rather
than as a wrong quota.

## What this version does not do

**Upstream's playground is not shipped.** Its production container proxies the collection and
document endpoints to `ingestor-server`, which this port does not deploy: `ingestor_server` needs
`nv_ingest_client`, and the extraction stage it drives is four NIM containers — the whole licence
problem this listing exists to avoid. The console here is ours, over the open components: a
sign-in form, a chat with citations, a document panel, and two panels naming what touched the data
and what the gate decided. Upstream's frontend is kept intact in the fork.

**The comparison against the NIM extraction pipeline is not measured** — that needs an NGC
credential nobody here has, and it is SUP-883. What is measured is this stack against
Unstructured and against text-only extraction, in `bakeoff/` of the source repository.

## The deployment

Six components, ten pods, one public hostname.

```
                        ┌──────────────────────────────────────────────┐
  the one hostname ───► │ console — sign-in form, session cookie       │
                        │           chat, citations, documents, panels │
                        └───────┬──────────────────────────┬───────────┘
                                │                          │
                        ┌───────▼────────┐        ┌─────────▼──────────┐
                        │ server         │        │ corpus             │
                        │ the ported     │        │ reads the gated    │
                        │ pipeline       │        │ source, indexes it │
                        └───┬───┬───┬────┘        │ and the files you  │
                            │   │   │             │ submit             │
                            │   │   │             └──┬────┬────────┬───┘
                            │   │   │                │    │        │
             ┌──────────────▼┐ ┌▼───────────┐ ┌───────▼──┐ │ ┌──────▼────────┐
             │ inference     │ │ reranker   │ │ embeddings│ │ │ the gatekeeper│
             │ vLLM, 1 GPU   │ │ CPU        │ │ CPU       │ │ │ egress        │
             └───────────────┘ └────────────┘ └───────────┘ │ └───────────────┘
                            │                              │
                     ┌──────▼───────┐  ┌──────────┐  ┌──────▼──────────────┐
                     │ vectors      │  │ cache    │  │ ingest              │
                     │ Milvus       │  │ Valkey   │  │ Docling, TableFormer│
                     └──────────────┘  └──────────┘  │ RapidOCR — CPU      │
                                                     └─────────────────────┘
```

Only the console is on the hostname. The retriever has no authentication of its own, which is why:
the session check in front of it is the whole of the protection, and the one route outside it is a
health probe that reports liveness and nothing about the corpus. The credential is collected with a
form and carried in an `HttpOnly; Secure; SameSite=lax` cookie — HTTP Basic does not survive a
single-page console, because the manifest and the icons are fetched in a mode where the browser
attaches no credentials, and ingress-nginx strips `Authorization` when it performs basic auth
itself.

The console and the indexer run from the pipeline's image with their code taken from a ConfigMap
the cloud digests and signs. That is deliberate: a second image would mean a tenant verifying this
deployment had to take another digest on trust, where this way the code that decides who may read
the corpus is in the snapshot in full.

## The digest

`evidence.expectedDigest` describes the **gated configuration**: a deployment with a dataset
attached to the `corpus` slot.

`d6abfe09887cf1199d1b774b82fa27eb9da02259414546ee3b67d17d60b4db6a`, measured on two deployments
of 0.1.8 on the `gpu-blueprints` cloud on 2026-11-23 — `rag-889c` and `rag-889d`, two hostnames in
two namespaces, each with the handbook dataset attached — which published byte-identical signed
snapshots of 37 resources, the second reading `matchesExpected: true`. Five more objects than
0.1.7's 32, and they are exactly the extraction component: a Deployment, a Service, two ConfigMaps
and a Secret.

It was measured once per version, seven times: `69a1160a…` (0.1.2), `6fc8b2bf…` (0.1.3),
`1370c19e…` (0.1.4), `03db53be…` (0.1.5), `94f3ab09…` (0.1.6, one deployment and never declared),
`9cf45e56…` (0.1.7) and this. Each of those versions changed a probe, a ConfigMap or an
environment variable, all of which are fields of objects in the snapshot — so a digest is
re-measured rather than carried forward, and each pair being byte-identical is the property
actually being claimed.

**One number from this round is worth more than the digest.** `215b22c0…` was 0.1.8 with the
extraction component on `swarm-doc-ingest` 0.2.0, and it is a perfectly good digest of a
deployment in which that component never ran: 0.2.0 declared no `nvidia.com/gpu` on its
container, and in a cluster space that has a GPU a container which leaves an extended resource
unsaid does not become ready. Nine pods up, one at `0/1`, the render attestable and the
component dead. Chart 0.3.0 declares the device as `0` — every other container in this listing
already did — and that is the version this digest describes. An attested deployment is a
deployment whose manifests are known, not one whose pods are working; this listing has now
learned that twice.

**Which configuration is a decision, not a default.** A deployment that mounts gated data gains
four objects the marketplace adds itself — the egress sidecar's ConfigMap, Deployment and Service,
plus the Secret holding its client certificate — and the cloud digests them, so a gated deployment
and an ungated one cannot share a digest. That is platform-wide and true of every gated listing
here. The gated one is declared because of what the gate does with it: the marketplace hands the
gate the `expectedDigest` of the listing version a deployment was created from, and the gate
fetches that deployment's own evidence and compares. Declaring the ungated digest would mean every
**gated** deployment of this listing is refused — which is the one thing it exists to do.

What that costs: an ungated deployment does not match the number on its own listing page. It needs
no gate, so nothing refuses it. The ungated digest was `3dde9a40…` on chart 0.1.1 and is not
declared.

What makes the number a property of the *version* rather than of the deployment: the hostname is
the only field that differs between two consumers, and it appears in exactly one object. It is
excluded on the Ingress itself and in `evidence.exclude`, because the listing page shows only what
the definition declares — and a digest displayed beside "no exclusions" answers the only question
that matters about it wrongly.

## Charts

| Chart | What it is |
|---|---|
| [`confidential-rag`](../../charts/confidential-rag/) | The pipeline, the console, the corpus indexer and the summary-status store. |
| [`text-embeddings-server`](../../charts/text-embeddings-server/) | An open embedding or reranking checkpoint on Text Embeddings Inference. Deployed twice. |
| [`milvus-standalone`](../../charts/milvus-standalone/) | Milvus as one pod — embedded etcd, local segment storage, authentication on. |
| [`swarm-model-server`](../../charts/swarm-model-server/) | The open-inference component (SUP-839). |
| [`swarm-doc-ingest`](../../charts/swarm-doc-ingest/) | Document extraction — layout, table structure and OCR on open components, CPU-only (SUP-874). |
