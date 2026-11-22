# Listings ported from someone else's open-source work

Rules for every listing that is a port of a third-party open-source reference architecture —
today, the NVIDIA AI Blueprints (epic SUP-837). Each such listing is a derivative of somebody else's
Apache-2.0 code, usually serves weights under a vendor's model licence, and describes an
architecture whose name is a trademark we do not hold. A listing that cannot meet a rule below is
not published until it can.

Agreed in SUP-841 (2026-11-22). Anything marked **lawyer** in §6 is open and is not to be decided
by judgement in a pull request.

## 1. Naming

The thing worth selling is the confidential execution, not the borrowed brand. A listing names a
product of ours and states where it came from — it never implies a relationship with the upstream
vendor.

| Field | Rule | Confidential RAG |
|---|---|---|
| `metadata.title` | `Confidential <what it does>`. Never `NVIDIA`, `Blueprint`, `NIM`, `AI Enterprise`, or a vendor model family name | `Confidential RAG` |
| `metadata.name` | the title as a slug; no vendor name | `confidential-rag` |
| `metadata.description` | what the tenant gets, then **one** provenance sentence (below) | see §5 |
| `metadata.sourceRepo` | **our** fork, the code the images are built from — not the upstream repository | `github.com/Super-Protocol/<fork>` |
| `metadata.tags` | no `nvidia`, `blueprint` or `nim` tags | — |
| chart, image and Helm release names | no vendor name. Upstream's `nvidia-blueprint-rag` chart is renamed in the fork | `confidential-rag` |
| README | opens with the provenance block (below) | — |

**Provenance sentence** (description, verbatim form):

> Based on the open-source NVIDIA &lt;X&gt; reference architecture (Apache-2.0), rebuilt on open components.

**Provenance block** (top of the listing README):

> Based on the open-source [NVIDIA &lt;X&gt; reference architecture](&lt;upstream URL&gt;), Apache-2.0,
> forked at &lt;tag / commit&gt; and modified by Super Protocol — see `MODIFICATIONS.md` in
> [our fork](&lt;fork URL&gt;). NVIDIA is a trademark of NVIDIA Corporation. This listing is not
> produced, endorsed or supported by NVIDIA.

When upstream is archived or unmaintained — RAG is, since 2026-11-05 — the block says so and says
that we maintain the fork. We do not imply we track an upstream that no longer moves.

Allowed, because they are factual: "based on", "ported from", "derived from" the NVIDIA reference
architecture; naming a model by its real name in a parameter label (`Nemotron 3 Embed 1B`); "runs on
NVIDIA H200". Not allowed: "NVIDIA Blueprint" as a product name, "official", "certified",
"validated by NVIDIA", "powered by NVIDIA Blueprints", NVIDIA logos or trade dress, and
"Confidential Blueprints" in any external material — it is an internal epic name only.

## 2. Apache-2.0 obligations in the fork (§4 of the licence)

Every port is built from a fork under `Super-Protocol/`. The fork must, before its first image is
pinned in a listing:

1. **Keep `LICENSE` at the root, unmodified** — including the upstream copyright line — and every
   third-party licence file upstream ships (`LICENSE-3rd-party.txt`,
   `frontend/LICENSE-3rd-party.txt`, the Helm chart's own `LICENSE`, the inline third-party list in
   vulnerability-analysis's `LICENSE`, single-cell's licence PDF). §4(a).
2. **Mark modifications.** A file we change keeps its SPDX header and gains one line beneath it:
   `Modified by Super Protocol, <year>: <what changed>`. A root `MODIFICATIONS.md` records the
   upstream tag and commit forked from, and what we changed at the level a reader cares about
   (NIM images removed, vector store switched, chart renamed, sample data removed). §4(b).
3. **Keep every copyright, patent, trademark and attribution notice** in the source. Never strip an
   SPDX header, including with a formatter. §4(c).
4. **Carry `NOTICE` verbatim when upstream has one.** None of the five blueprints this epic ports
   ships a `NOTICE` file anywhere in its tree (rag, single-cell-analysis-blueprint,
   vulnerability-analysis, transaction-foundation-model, ambient-healthcare-agents — checked
   2026-11-22). Check again at fork time and at every upstream sync; if one appears, it goes into
   the fork and into every image built from it. §4(d).
5. **Ship the licences inside the images.** An image is redistribution in object form: copy
   `LICENSE`, the third-party licence files and `MODIFICATIONS.md` (and `NOTICE`, if any) to
   `/licenses/` in every image we build from the fork. A GPL binary in the image — vulnerability-
   analysis carries `wget`, GPLv3 — is named in that directory's third-party list.
6. **Upstream code copied into this repository** (rather than referenced through the fork's images
   or charts) carries the same `LICENSE` beside it, and the same modification marks.

**Not everything in an Apache-2.0 repository is Apache-2.0.** Read every licence file, not only the
root one:

- `rag/data/` is governed by `data/LICENSE.DATA`, the **NVIDIA Asset License: non-commercial
  research or evaluation only, no production use, no redistribution.** The fork deletes `data/`.
  Those files never go into an image, a default dataset, a demo or a screenshot. Demo corpora are
  ours or carry a licence that permits commercial use.
- Sample datasets the single-cell notebooks download (CELLxGENE and similar) carry their own terms;
  check each before it appears in a demo.
- A sibling repository is not evidence: `streaming-data-to-rag` lives in the same org and is under
  the NVIDIA Software License Agreement. Read the `LICENSE` of each repository, every time.

## 3. Model weights: the tenant sees the licence before they deploy

The software and the weights are licensed separately, and the listing page shows both.

- `metadata.license` is the licence of the **software stack** the listing deploys — `Apache-2.0`
  for these ports. It is a weight licence only on a single-model listing, as with
  `gemma-2-2b-it`.
- **Every model choice states its licence in the option label**, the pattern the open-inference
  component already uses (`"Qwen3-Coder 30B A3B FP8 — … (Apache-2.0)"`). A listing with a fixed model names the
  model and its licence in the description.
- **The README has a "What you are agreeing to" section**: each set of weights the listing can
  serve, its licence with a link, and the conditions that bite — Llama: under 700M monthly active
  users, "Built with Llama", acceptable-use policy; NVIDIA Open Model License and OpenMDW-1.1: link
  and summary of attribution and use restrictions.
- **Default to weights with no extra terms** (Apache-2.0 / MIT: Qwen3, bge-m3, bge-reranker), so the
  one-click deployment adds nothing to what the tenant agrees to. Vendor-licensed weights are an
  opt-in choice, labelled as such.
- The licence is also on the evidence: the model-server chart renders it into `model-licence.json`,
  which the deployment's digest covers. The listing page and the evidence must agree.
- **Never offered:** non-commercial weights (`nvidia-oneway-noncommercial-license` — Audex;
  `coqui-public-model-license` — XTTS-v2), and the NemoGuard weights (`license: other`) until §6
  clears them.

## 4. What we add, what we give up — for partners

Use verbatim or shorten; do not embellish.

> **Same open reference architecture. Run where your data is allowed to be.**
> We take NVIDIA's open-source reference architectures and run them inside confidential hardware.
> Three things the original does not give you: the data is processed where neither we nor the
> cloud operator can see it; every deployment publishes cryptographic proof of exactly which code
> and which model are touching it; and the data owner approves that exact build and can cut access
> the moment it changes.
>
> **What we give up by dropping NVIDIA's NIM containers:** NVIDIA's tuned serving stack and its
> throughput, NVIDIA's enterprise support and release cadence, and a few NVIDIA-only components
> (speech, safety models, the extraction pipeline) that we replace with open equivalents. **What
> that buys:** no NVIDIA AI Enterprise licence — about $4,500 per GPU per year — and no closed
> container in the middle of a system whose whole point is that you can verify what runs.
>
> This is our port, maintained by us. It is not an NVIDIA product and is not endorsed by NVIDIA.

## 5. Confidential RAG — approved listing text

SUP-840 uses this text; the CTO checks the merged `app.yaml` and README against §1–§3 before the
listing is published.

```yaml
metadata:
  name: confidential-rag
  title: Confidential RAG
  description: >-
    Ask questions over your own documents without them ever leaving confidential hardware. Every
    deployment publishes cryptographic proof of which embedder and which model touch your data, and
    the data owner can withdraw access the moment that changes. Based on the open-source NVIDIA RAG
    reference architecture (Apache-2.0), rebuilt on open components.
  sourceRepo: https://github.com/Super-Protocol/<fork>
  license: Apache-2.0
  tags: [ai, rag, retrieval, attestation, confidential]
```

README provenance block: §1, with `<X>` = `RAG`, the upstream URL
`https://github.com/NVIDIA-AI-Blueprints/rag`, forked at release `2.6.2`, and a sentence that the
upstream was archived by NVIDIA on 2026-11-05 and the fork is maintained by Super Protocol.

## 6. For a lawyer, not for a pull request

Escalated to Denis on SUP-841. Until answered, the listing takes the conservative branch in brackets.

1. **Are we a distributor of the weights?** The marketplace pulls weights into a tenant's enclave on
   infrastructure we operate. Whether that makes us a redistributor under the NVIDIA Open Model
   License, OpenMDW-1.1 and the Llama Community License decides which attribution and pass-through
   duties fall on us rather than the tenant. *(Conservative branch: Apache-2.0/MIT weights by
   default; vendor-licensed weights opt-in with their licence surfaced per §3.)*
2. **NVIDIA Open Model License specifics** — its attribution, trademark and termination clauses
   applied to a hosted multi-tenant platform, before any Nemotron model becomes a default.
   *(Not a default until answered.)*
3. **NemoGuard weights** (`license: other`, plus the Llama 3.1 licence downstream). *(Not offered.)*
4. **The disclaimer wording in §1** — confirm it is sufficient for nominative use of "NVIDIA" on the
   listing page and in partner material. *(Used as written meanwhile; it is conservative.)*
