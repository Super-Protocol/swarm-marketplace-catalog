# transaction-model-notebook

> Based on the open-source [NVIDIA transaction-foundation-model developer example](https://github.com/NVIDIA-AI-Blueprints/transaction-foundation-model),
> Apache-2.0, forked at commit `04ffa4d3f216b2b6d21bc875ef3c815cbf6b3547` and modified by Super
> Protocol — see `MODIFICATIONS.md` in
> [our fork](https://github.com/Super-Protocol/confidential-transaction-model) and
> `files/notebooks/PROVENANCE.md` here. NVIDIA is a trademark of NVIDIA Corporation. This chart
> is not produced, endorsed or supported by NVIDIA. Upstream calls it a **developer example**
> rather than a blueprint, and so do we.

A GPU JupyterLab for transaction foundation models: our own workbench image (RAPIDS, torch and
NeMo AutoModel, every wheel pinned by sha256, the 29M-parameter checkpoint baked in), and the
five notebooks of NVIDIA's developer example ported to read the transactions the deployment is
bound to instead of downloading a public corpus.

Deployed by `apps/confidential-transaction-model`. Seven objects: two ConfigMaps, a Secret, a
PVC, a Deployment, a Service and an Ingress.

## The four decisions worth knowing before changing anything here

### 1. The environment is in an image, not installed at boot

`rapids-singlecell-notebook`, the other notebook chart here, installs its Python environment at
container start from a hash-pinned lock in a ConfigMap, and its README says why: this repository
had no image CI, and a hand-built image would have replaced a pin anybody can verify with one
only we can reproduce.

That trade has changed, which is what makes this chart different. The fork
[`Super-Protocol/confidential-transaction-model`](https://github.com/Super-Protocol/confidential-transaction-model)
builds `transaction-workbench` in GitHub Actions with a build attestation, exactly as the RAG
and vulnerability-analysis forks do, and `scripts/verify-pins` traces the digest this chart pins
back to the commit that produced it. So the environment is in the image — and it has to be:

- **torch, NeMo AutoModel and their CUDA libraries are about 5 GB of wheels.** A boot-time
  install of that is minutes of pod start-up, every restart, inside an enclave whose egress is
  deliberately narrow.
- **The checkpoint is 56 MB over Git LFS upstream.** A runtime download is not in the attested
  render, which is the whole product claim.
- **torch's bundled CUDA has to be repointed at the conda runtime before anything imports.**
  That is a build step, not something a `pip install` in a ConfigMap can do — see the fork's
  Dockerfile, where it is the difference between `import cuml` working and raising
  `undefined symbol: cublasLtZZZMatmulAlgoGetHeuristicForStream`.

What is still in a ConfigMap is what is this *deployment's*: `bootstrap.sh`, `tfm_workspace.py`
and the notebooks. Those are the files most likely to need a fix, they are small, and the cloud
digests them — so a fix reaches an existing deployment when the pod rolls, without rebuilding
9 GB.

### 2. `tfm_workspace.py` is the whole of the port, and it holds one thing nobody else does

The ported notebooks call `load_transactions()` where upstream called `urlretrieve`. That part
is bookkeeping: read the binding or the uploaded extract, rename the tenant's columns onto the
names upstream's tokenizer expects, derive `Year`/`Month`/`Day`/`Time`, sort by customer and
time.

`check_coverage()` is the part with no counterpart upstream. The shipped checkpoint was
pretrained with a **fixed** 6,251-token vocabulary: US state abbreviations, 3-digit ZIP
prefixes, a fixed MCC list, seven dollar-amount buckets, customer indices 0–2999. Feed it a
European bank's transactions and every one of those fields tokenises to its catch-all, the model
returns embeddings that look exactly like embeddings, the UMAP looks like a UMAP, and XGBoost
reports an AUROC. Nothing fails. So the coverage is measured per field, printed, and raises below
the deployment's floor (`vocabulary.minCoverage`, 50% by default), in three places: the first
notebook, the load in `01`, and `04` before the first embedding is extracted. `04` also asserts
that the tokenizer's vocabulary size equals the checkpoint's embedding-table height, because ids
from a tokenizer that built a different vocabulary index the wrong rows and raise nothing.

The honest answer for a tenant whose data does not fit is `03`, which pretrains with a
vocabulary built from their own fields and does not use the shipped weights. That is a real
option rather than a consolation: a model trained inside the enclave on transactions that may
not leave it is the product.

### 3. `/login` is the probe, and `/api/status` is not

`jupyter_server` answers `/api/status` with 403 to an unauthenticated caller, so a probe on it
marks the pod unready for ever while the server behind it serves perfectly. `/login` is the one
route it serves without a credential. Measured on the single-cell chart's image, 2026-11-22, and
inherited here deliberately.

The startup probe allows 15 minutes. Nothing is installed at boot here, but the image is ~9 GB
of compressed layers and the first pod on a node pays for the pull.

### 4. Nothing attested varies between two deployments

Two deployments of one chart version must publish the same evidence digest, or
`evidence.expectedDigest` in the listing means nothing and the gatekeeper has nothing to hold a
deployment to. Three things vary per deployment and each is dealt with:

- the **sign-in token** is in a Secret, whose values are lifted out of the snapshot;
- the **dataset binding** — the dataset owner's credentials — is in the same Secret, which is
  also why attaching a dataset changes nothing the cloud digests (`charts/tests/run.sh` diffs the
  bound and unbound goldens to prove it);
- the **hostname** is in the Ingress and is excluded, by the chart's
  `swarm.io/exclude-evidence-fields` annotation *and* by the listing's `evidence.exclude` — the
  listing page shows only what the definition declares.

The column mapping and the row ceiling are ordinary environment values on purpose: they are not
secret, and two deployments that chose the same schema should attest the same thing.

## Authentication

`jupyter_server` serves a sign-in **form** and sets a session cookie. That matters: HTTP Basic
does not survive a single-page console — the browser attaches no credential when fetching a
manifest or an icon, and ingress-nginx clears `Authorization` when it performs basic auth itself
(marketplace trap 14).

The token reaches the container as `JUPYTER_TOKEN`, from the Secret, which `jupyter_server` reads
by itself. Never as a command-line argument: arguments are rendered into the manifests and
published in the evidence snapshot in clear. The chart refuses to render an ingress without a
token, because the RAPIDS base image's own command starts JupyterLab with
`--NotebookApp.token=''`.

The listing's `signIn` output deliberately does not hand out a `https://…/lab?token=…` link: a
query string lands in the gateway's access log, in any proxy in between and in the browser's
history. The form posts the same token in a body.

## The GPU

One device, `runtimeClassName: nvidia`, declared in request **and** limit — and declared as `0`
on a container that does not want one. A cluster space raised with a GPU carries a ResourceQuota
naming `nvidia.com/gpu`, and a container that leaves it unsaid takes the namespace default,
which is how SUP-890 spent three GPU deployments on one pod sitting at `0/1`.

`resources` is one map for requests and limits because a tenant cluster space sets
`maxLimitRequestRatio: 1`. Memory is host memory, not VRAM: the tokenised corpus is built in host
RAM before it reaches the card, and `shm.size` (torch's DataLoader workers, 8 GiB — upstream's own
`docker run` asks for the same) counts against that limit.

## What this chart deliberately does not do

- **It does not serve an API.** It is a workbench: a tenant runs notebooks in it. Serving the
  shipped checkpoint as an embedding endpoint was considered and rejected on SUP-870 — its
  usefulness on foreign transactions is exactly what nobody has established.
- **It does not read object storage.** The slot accepts `clickhouse-http/v1`, which is the only
  interface the marketplace gatekeeper can serve, and an extract can be uploaded through the
  browser. `s3fs` is not in the image because botocore pins `python-dateutil<3.0.0` and the base
  image carries 3.0.0 as a conda package underneath pandas — see the fork's `requirements.in`.
- **It does not download anything at run time.** `HF_HUB_OFFLINE=1`, `DO_NOT_TRACK=1`, no
  `%pip install` in any notebook, and the checkpoint is in the image.
