# swarm-doc-ingest

The document-extraction stage a RAG listing composes: layout analysis, table-structure
recognition and OCR on open components, emitting the element contract the NVIDIA RAG
reference architecture's retrieval stage already reads.

It replaces `nemo-microservices/nv-ingest` and the four extraction NIMs — Nemotron Page
Elements v3, Table Structure v1, Graphic Elements v1 and Nemotron OCR — with **no NIM
container, no NGC credential and no GPU**.

- **Source and image:** [`Super-Protocol/swarm-doc-ingest`](https://github.com/Super-Protocol/swarm-doc-ingest).
  The image is built and attested there, and the bake-off that chose the stack lives
  there too, under `bakeoff/`. This chart holds the deployment and the digest pin and
  nothing else.
- **Not a listing.** There is no Ingress template in this chart at all. The listing that
  composes it publishes its own front end; this answers inside the cluster space, and is
  still authenticated, because a namespace is not a trust boundary once a second
  workload is in it.

## What it extracts, and the one thing it does not

| | |
|---|---|
| In | PDF, DOCX, PPTX, XLSX, HTML, Markdown, text, and PNG / JPEG / TIFF / BMP scans |
| Out | nv-ingest `MetadataSchema` elements — text, tables as cell grids *and* Markdown, images with bounding boxes |
| Tables | extracted as structure, never flattened into prose |
| **Charts** | **located, not transcribed.** A chart comes back as an image element with its bounding box, not as a table of its series. A listing's copy must say "charts are cited as images" and not imply their data is searchable |
| Network | none at run time. The models are baked into the image and `HF_HUB_OFFLINE=1`, so a missing weight is a refusal at boot rather than a download while a tenant's document is in memory |
| GPU | not used, not wanted |

## The scores

Measured on 8 vCPU with no accelerator; the full 59-row sweep is `bakeoff/results.json`
in the source repository. Two corpora: one generated, so its ground truth is exact and
publishable, and the reference architecture's own three sample documents, whose
documents and transcribed grids are under the NVIDIA Asset License Agreement and are
therefore in neither repository.

| | table F1 | cell F1 |
|---|---|---|
| **This stack** — Docling, `heron` layout, TableFormer `accurate`, RapidOCR | **0.844** over ten documents | 0.892 |
| …on the reference architecture's own three documents | **0.935** | — |
| Unstructured `hi_res`, same documents, same scorer | 0.332 | 0.675 |
| …on the same three | 0.238 | — |
| **A text extractor with no layout or table model** | **0 tables out of 19** | — |

That last row is why this component exists. Text-only extraction reads born-digital
prose at a character error rate of 0.0000 and loses every table. "Tables extracted
rather than flattened into prose" is not a refinement of text extraction; it is a
different pipeline.

On `multimodal_test.pdf`, which states its own expected output in its conclusion —
*"2 tables, 2 charts, and some text including 3 bullet points"* — this stack finds two
tables of two, three list items of three, and three pictures: the two charts plus the
illustration the document also contains. **The comparison against the NIM pipeline run
directly is still not measured**, because that needs an NGC credential nobody has; see
the SUP-874 thread.

## Composing it

```yaml
components:
  - name: ingest
    source:
      type: helm
      repoUrl: https://super-protocol.github.io/swarm-marketplace-catalog/charts
      chart: swarm-doc-ingest
      version: "0.3.0"
    images:
      - name: ghcr.io/super-protocol/swarm-doc-ingest/ingest
        digest: sha256:a114f3df285a9c0f30e96e3bf2b31b04184ba5d956b007e8df81cc4a4db704d1
    deployment:
      values:
        base:
          # No public name of its own, and this chart has no Ingress to disable.
          apiKey: ""
          extraction:
            # Changing either of these changes what the component reads out of a
            # tenant's documents while the numbers above still claim the old
            # behaviour. The chart refuses a preset it does not bake.
            layoutPreset: layout_heron_default
            tableMode: accurate
            ocrEngine: rapidocr
          resources:
            cpu: "4"
            memory: 4Gi
            # Zero, and not optional in a cluster space that HAS a GPU: such a
            # space carries a ResourceQuota naming `nvidia.com/gpu`, the device
            # is an extended resource, and a container that leaves it unsaid
            # does not become ready. 0.2.0 omitted it and the pod sat at 0/1
            # with every other pod of the composing listing up (SUP-890).
            gpu: "0"
        patches:
          # One generated, sensitive parameter, patched into this component and
          # into whatever calls it. The marketplace lifts every rendered Secret
          # out of the bundle and seals it, so a generated value inside a Secret
          # costs the evidence digest nothing.
          - op: replace
            path: apiKey
            value: "{{ params.ingestKey }}"

outputs:
  - id: ingestBaseUrl
    title: Extraction endpoint (inside the cluster space)
    type: string
    value: "http://swarm-doc-ingest.{{ namespace }}.svc.cluster.local:8080"
```

`POST /v1/extract` takes a multipart `file` and returns `{"source_id", "source_name",
"elements", "stats"}`. `GET /v1/config` reports the model pins the deployment is
actually running — the same facts the chart renders into its ConfigMap and the cloud
signs, which is how you check the process agrees with the document that was signed.
`GET /v1/health` is the probe and the only endpoint that needs no token, because a
kubelet carries none.

## What this component contributes to `resources.min`

One pod, and **it rolls** — unlike `swarm-model-server`, which must use `Recreate`
because the card is held by the running pod. So a listing composing this counts its
demand once **and a surge pod**:

| | CPU | Memory | Volume |
|---|---|---|---|
| `ingest` | 4 | 4Gi | 1Gi (`emptyDir`, uploads) |
| one surge pod while it rolls | 4 | 4Gi | — |
| **contribution to the quota** | **8** | **8Gi** | **1Gi** |

and `maxPod` must be at least `4` / `4Gi`. The arithmetic for the whole listing, with
the inference component beside this one, is in
[`docs/open-inference-component.md`](../../docs/open-inference-component.md).

Those numbers are measured, not estimated: a 15-page PDF with a text layer peaks at
1.96 GiB in 13.1 s, the same 15 pages as a 200-dpi scan through OCR at 2.34 GiB in
52.6 s, and the highest figure anywhere in the sweep is 2.46 GiB. Memory is bounded
rather than linear in pages. On CPU: 8 cores 51.7 s, **4 cores 59.4 s**, 2 cores 93.1 s,
1 core 112.7 s — four cores give up 15% for half the quota.

**One document is extracted at a time**, and the rest queue. That is the memory
measurement rather than an unfinished feature: two in flight would exceed the 4Gi limit
sized from one, and a pod OOMKilled mid-request reads to a tenant as a broken service
rather than as a wrong quota. Scale with `replicaCount`, and add a surge pod per
replica.

## Changing the extraction configuration

`extraction.layoutPreset`, `tableMode` and `ocrEngine` are in a ConfigMap the cloud
digests and signs, so a tenant who approved a digest approved *these* presets. The chart
refuses an OCR engine the image does not carry, and it refuses every layout preset but
`layout_heron_default` — with no flag to override that, because there is nothing the
override could reach. The image bakes `docling-layout-heron` alone and runs with
`HF_HUB_OFFLINE=1`, so a pod asked for `egret_large` cannot fetch the weights and never
starts. The measurement that chose `heron` stands behind the refusal: `egret_large`, which
is better on every ruled table, scores **0.000** on a table drawn without ruling lines,
where `heron` scores 0.968.

**Serving a different preset is therefore an image change, not a value**: a
`swarm-doc-ingest` release that bakes those weights, fresh `bakeoff/bench.py` numbers, and
a new `image.digest` and `appVersion` here.

Re-run `bakeoff/bench.py` in the source repository before moving `tableMode` or
`ocrEngine`, and before moving the image digest. A render test and a readiness probe both
pass on a build that extracts nothing.

## The device, declared as zero

This component is CPU-only and wants no card. It says so anyway, in both the request and the
limit, because a cluster space raised **with** a GPU carries a ResourceQuota naming
`nvidia.com/gpu`: the device is an extended resource, and a container that leaves it unsaid
takes whatever the namespace defaults it to rather than nothing. 0.2.0 of this chart omitted it,
and in the Confidential RAG listing's GPU cluster space its pod never became ready — nine pods
up, this one at `0/1`, the render perfectly attestable and the component doing nothing (SUP-890).
Every other container in that listing, including the marketplace's own egress sidecar, already
declared `0`.

Set `resources.gpu` only if a future build of this component wants the card, which would also
need `runtimeClassName: nvidia` and a re-run of the bake-off.

`OMP_NUM_THREADS` is not configured, it is derived: the `resources.cpu` quota in whole
cores, rounded up, never below 1. The quota is a Kubernetes quantity, so `1500m` is one
and a half cores and renders as 2 threads — not 1500, which is what stripping the `m` once
produced and which would be the slowest extraction this chart can run (SUP-882). Set
`ompNumThreads` only to override the derivation.
