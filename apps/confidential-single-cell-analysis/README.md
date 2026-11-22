# Confidential Single Cell Analysis

GPU-accelerated single-cell RNA analysis on expression matrices that are not
allowed to leave the building.

A deployment is one JupyterLab workspace with a card attached, the
RAPIDS-singlecell stack on it, and four notebooks: the end-to-end scRNA workflow
(QC, filtering, normalisation, PCA, neighbours, UMAP and t-SNE, Louvain and
Leiden, Harmony batch correction, marker genes), transcription-factor and
pathway activity with decoupler, the same workflow with Pearson residuals, and
one of ours that says what the deployment is holding and proves the GPU is
reachable before any of it starts.

## Where it comes from, and what it is not

The workflow is NVIDIA's [Single-Cell Analysis
Blueprint](https://github.com/NVIDIA-AI-Blueprints/single-cell-analysis-blueprint),
Apache-2.0, built on [RAPIDS-singlecell](https://rapids-singlecell.readthedocs.io/)
(MIT) by [scverse](https://scverse.org). This listing is a port of it, not a
distribution of it, and it is **not an NVIDIA product**: there is no
relationship, no endorsement and no NVIDIA support behind it. The container's own
licence says the same thing in §4(d), and the guardrails for this epic (SUP-841)
say it as a naming rule.

`charts/rapids-singlecell-notebook/files/notebooks/PROVENANCE.md` lists every
change made to the upstream notebooks, carries upstream's licence, and is copied
into every deployment's workspace.

## Licences a tenant is agreeing to

| Layer | Licence |
|---|---|
| The ported notebooks, the chart, this listing | Apache-2.0 |
| RAPIDS-singlecell, scanpy, anndata, decoupler, the lock's 29 wheels | MIT / BSD-3 / Apache-2.0 / MPL-2.0 |
| The container they run in — `rapidsai/notebooks` 26.02 | **NVIDIA Deep Learning Container License** |

The last row is the one the stage-1 audit (SUP-838) did not find, and it is on
the card through the `containerLicence` output rather than in a footnote. It is
**not** NVIDIA AI Enterprise: no entitlement, no NGC credential, no per-GPU fee,
and the Docker Hub copy this listing pins pulls anonymously. What it does ask of
whoever deploys it: material additional functionality beyond the container
(§2(a)), no claim of NVIDIA sponsorship or endorsement (§4(d)), and NVIDIA's own
statement that the container is not certified for medical or other life-critical
use (§4(h)). There is no model and there are no weights in this listing at all,
so the question that dominates the rest of this epic — what the weights are
licensed for — does not arise.

## The data

One slot, `matrices`, optional, and two interfaces behind it:

- **`clickhouse-http/v1`** — the gateable one. A dataset published
  `access.mode: gateway` is never handed over as a connection: this deployment
  gets a plain listener inside its own namespace which re-originates every
  request to the gatekeeper with the deployment's own certificate, and the gate
  admits each read against the evidence the deployment published. Change the
  deployment and the data stops being served. The matrix is assembled from
  long-form `sc_counts` (`cell_id`, `gene`, `count`) plus `sc_cells` and,
  optionally, `sc_genes`.
Attaching nothing is supported and useful: upload an `.h5ad` into
`/workspace/data` through the browser and the data is never registered with a
marketplace at all. That is also, today, the only way to work from an `.h5ad`.

**Why there is no `.h5ad` slot yet.** `.h5ad` is the format a lab's matrices
already exist in, the contract is written in `interfaces/s3-h5ad/v1.yaml`, and
`sc_workspace` reads it (tested against object storage by prefix and by name) —
but this repository is not the interface registry. A marketplace matches against
`BUNDLED_INTERFACES` in the platform's `marketplace-spec` library, mirrored from
the specification repository, and `AppPublishingService.submit()` refuses any
definition naming a contract the registry does not hold. Naming it would make
this listing unpublishable everywhere. Registering it is a change to the
specification repository, then to swarm-cloud's mirror, then to this copy and
this listing's `accepts` — and worth doing, because every S3-shaped dataset in
this catalogue has the same problem one format along.

**The gatekeeper caveat, stated plainly.** The gate speaks ClickHouse over HTTP
and only that — it injects `x-clickhouse-user` / `x-clickhouse-key` into every
request it forwards and rewrites the database the caller named
(`marketplace-gatekeeper/src/app/forwarder.ts`). An `s3-h5ad` dataset therefore
cannot be gated today, whatever its `access.mode` says. Gating object storage is
a platform change, not a listing one.

## Evidence

`evidence.expectedDigest` is measured on real deployments of this version, not
derived from the charts. Two things make it stable:

- the hostname is excluded, in the chart's annotation and in this definition;
- everything else that varies per deployment — the sign-in token and the whole
  dataset binding — is in a Secret, and a Secret's values never reach the
  snapshot. `charts/tests/run.sh` diffs the bound and unbound renders to keep it
  that way.

The declared digest is
`3e03a7196d77d9e0b9f8340a976d372337fc2e5474f591530e6f84b41730979a`, measured on
2026-11-22 from the signed evidence of two deployments on the `gpu-blueprints`
cloud — `sc-842a` and `sc-842b`, two hostnames in two namespaces. The snapshots
were byte-identical: six resources each (two ConfigMaps, the Deployment, the
Ingress, the Secret, the Service).

One caveat that belongs to the platform rather than to this listing: a
deployment that mounts **gated** data gains three objects the marketplace adds
itself (the egress sidecar's ConfigMap, Deployment and Service), so its snapshot
is not the snapshot of a deployment with nothing attached. The digest above is
the no-gated-data configuration, which is what every deployment of this listing
is today. The first gated deployment has to re-measure and re-declare — and
that is true of every gated listing in this catalogue, not only this one.

## What it deliberately does not do

- **It does not download the blueprint's example corpus.** Upstream's notebooks
  begin by fetching `dli_census.h5ad` from the public internet. Here they read
  what the operator attached. A tenant who wants to try the listing against
  public data can still download one by hand from the terminal — it just is not
  what the workflow does.
- **It serves no model and exposes no inference endpoint.** There is nothing in
  it for an LLM to do.
- **It does not ship four of the blueprint's seven notebooks** — the
  11-million-cell out-of-core workflow (wants a multi-GPU system; unverified on
  one card), the spatial demo (a different dependency closure and a different
  kind of data), and the two built around specific public datasets. Reasons per
  notebook are in `PROVENANCE.md`.
- **It is not a cage around the tenant's own operator.** A notebook is a shell.
  What the deployment guarantees is that the platform moves no data out and that
  the gatekeeper can refuse to serve a deployment whose evidence changed.
- **One notebook needs outbound HTTPS, and says so above the cell that does.**
  `02_regulatory_activity.ipynb` downloads the CollecTRI regulon table from
  Zenodo — public reference data, a plain GET, nothing of yours in it — so that
  notebook fails in a space with no egress, and that table is not covered by the
  attested lock. `01_preprocessing` and `03_pearson_residuals` read and write
  the workspace only.
- **It does not promise clinical validity.** It is research tooling, and the
  container it runs in is explicitly not certified by its vendor for medical use.

## Sizing

8 CPU / 48 GiB / 1 GPU for the workspace, and the quota is declared one CPU and
2 GiB above that because the marketplace adds its own egress sidecar to any
deployment that mounts gated data. 100 GiB of workspace volume, which is the
order the blueprint's own reference instance uses. `minVram` is 24 GiB — the
blueprint's floor for the notebooks shipped here, which keeps an L40S or an A10
a legitimate target rather than only a datacentre card.

Measured on one `NVIDIA H100 NVL` (95 830 MiB, SUP-868). `resources.gpu.types`
names the exact `nvidia-smi` product strings this estate has; a card of the same
family under a different product name needs its own entry, and a guess there
breaks placement.
