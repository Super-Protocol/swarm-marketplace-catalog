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

- **`s3-h5ad/v1`** — the native one, and as of SUP-881 a registered contract
  rather than a copy in this repository. One object is one annotated matrix, so
  there is no sidecar to keep in step: `sc_workspace._read_h5ad_from_s3` reads
  the objects the connection names, or every `.h5ad` under its prefix when it
  names none. The dataset carries its own sealed credentials and the deployment
  reads the bucket directly, which is why this interface is second in `accepts`
  — preference order reads "gate it if the dataset can be gated".

The slot's schema constraint applies to both, because a slot's schema is
semantic rather than per-interface. One `.h5ad` object carries exactly the
`cell_id` / `gene` / `count` triple the constraint asks for, but a dataset that
never declares a schema is not bindable here on either interface.

Attaching nothing is supported and useful: upload an `.h5ad` into
`/workspace/data` through the browser and the data is never registered with a
marketplace at all.

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
`9942e5d8956673b4d272f381c0c362727323e19f4dccddc87509c68d2301791f`, and it describes the
**gated** configuration: a deployment with a `clickhouse-http/v1` dataset attached. Measured on
2026-11-24 from the signed evidence of two deployments on the `gpu-blueprints` cloud —
`sc-900a` and `sc-900b`, two hostnames in two namespaces, each with
[Kestrel PBMC Immune Panel](../../datasets/kestrel-pbmc-panel/) bound. The snapshots were
byte-identical: the same 121,459 bytes of resource JSON, 279 JSON Pointers each, zero differing
fields. **Ten** resources: 3 ConfigMaps, 2 Deployments, the Ingress, 2 Secrets, 2 Services.

It is the gated number on purpose. The marketplace hands the gate the `expectedDigest` of the
version a deployment was created from, and the gate fetches that deployment's own evidence and
compares — so the only deployments this value is ever checked against are the ones that mount
gateway-mode data. An ungated number would refuse every gated read, which is the one thing the
`clickhouse-http/v1` slot exists for.

What that costs: a deployment with an `.h5ad` dataset, or with nothing attached, does **not**
match the number on this page. Nothing refuses it — there is no gate in front of it to do the
refusing. The ungated digest, for the record, is
`3e03a7196d77d9e0b9f8340a976d372337fc2e5474f591530e6f84b41730979a`: six resources, measured on
`sc-842a` / `sc-842b` on 2026-11-22, and not declared.

The four extra objects are the egress sidecar's ConfigMap, Deployment and Service **and the
Secret holding its client certificate**. Four, counted on a published snapshot — an earlier
version of this file said three, which was a reading of the platform's source rather than a
count.

A real read, inside a deployment, through the sidecar: `sc_workspace.load_matrix()` over
`clickhouse-http/v1` assembled the whole 1,800 × 933 matrix from `sc_counts`, `sc_cells` and
`sc_genes` — 1,101,372 non-zero counts, nine annotated populations, `batch` and `cell_type`
resolved. The connection it opened names
`confidential-single-cell-analysi-gatekeeper-egress` and carries no credential of the lab's.
Admission is not instant: for a few minutes after a number is declared the read fails with a
`502` from the deployment's own egress sidecar, because the gate has not yet been told about the
client.

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
