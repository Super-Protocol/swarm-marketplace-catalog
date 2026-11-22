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

**What we give up:** NVIDIA's tuned serving stack and its throughput, NVIDIA's enterprise support
and release cadence, and the extraction pipeline (see "What this version does not do"). **What that
buys:** no NVIDIA AI Enterprise licence — about $4,500 per GPU per year — no NGC credential for a
tenant to hold, and no closed container inside the attestation.

**The comparison against NIM is not measured, and this says so rather than guessing.** Nobody on
this project has an NGC credential, so no NIM deployment has been run on this hardware. The
regression against NIM's TensorRT-LLM engine is an open number; what is measured is in
[`docs/open-inference-component.md`](../../docs/open-inference-component.md).

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

## What this version does not do

**Files.** 0.1.0 reads documents from a table, not from PDFs, DOCX or slides. The extraction stage
that handles files — Docling, TableFormer and RapidOCR, CPU-only, chosen on measurements in SUP-874
— is not published yet. Its footprint (4 CPU, 4Gi, a 1Gi model volume) is already reserved in
`resources.min`, because a cluster space's quota is fixed when it is created and a reconfigure
re-renders the version the deployment was created from: a deployment of 0.1.0 can therefore take
that component when it lands, and one whose quota was sized exactly could not.

**Charts are not transcribed.** When the extraction stage does land, a chart in a document is
located and cited as an image; its series are not searchable. NVIDIA's Graphic Elements NIM is what
turns a chart into a table of numbers, and there is no open equivalent in this stack.

**Upstream's playground is not shipped.** Its production container proxies the collection and
document endpoints to `ingestor-server`, the service this port has not substituted yet, so shipping
it would publish a console whose document panel cannot work. The console here is ours: a sign-in
form, a chat with citations, and two panels naming what touched the data and what the gate decided.
Upstream's frontend is kept intact in the fork for the release that has an ingestor to point it at.

## The deployment

Five components, eleven pods, one public hostname.

```
                        ┌──────────────────────────────────────────────┐
  the one hostname ───► │ console — sign-in form, session cookie       │
                        │           chat, citations, status panels     │
                        └───────┬──────────────────────────┬───────────┘
                                │                          │
                        ┌───────▼────────┐        ┌─────────▼──────────┐
                        │ server         │        │ corpus             │
                        │ the ported     │        │ reads the gated    │
                        │ pipeline       │        │ source, indexes it │
                        └───┬───┬───┬────┘        └────┬──────────┬────┘
                            │   │   │                  │          │
             ┌──────────────▼┐ ┌▼───────────┐ ┌─────────▼──┐  ┌────▼──────────┐
             │ inference     │ │ reranker   │ │ embeddings │  │ the gatekeeper│
             │ vLLM, 1 GPU   │ │ CPU        │ │ CPU        │  │ egress        │
             └───────────────┘ └────────────┘ └────────────┘  └───────────────┘
                            │
                     ┌──────▼───────┐    ┌──────────┐
                     │ vectors      │    │ cache    │
                     │ Milvus       │    │ Valkey   │
                     └──────────────┘    └──────────┘
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

`6fc8b2bf6569326c04e9766fdaa10c96b34ba07cf6b2ab459a6ed116e192ea36`, measured on two deployments of
0.1.3 on the `gpu-blueprints` cloud — two hostnames, two namespaces — which published
byte-identical signed snapshots of 32 resources. (0.1.2's was `69a1160a…`, measured the same way;
it moved because 0.1.3 changes three probes, and a probe is a field of a Deployment the cloud
digests.)

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
