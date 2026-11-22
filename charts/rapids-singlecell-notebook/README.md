# rapids-singlecell-notebook

A GPU JupyterLab for single-cell analysis: the RAPIDS notebooks image, the
RAPIDS-singlecell environment installed from a hash-pinned lock, and the
notebooks of NVIDIA's Single-Cell Analysis Blueprint ported to read a dataset
the deployment is bound to instead of downloading a public corpus.

Deployed by `apps/confidential-single-cell-analysis`. Seven objects: two
ConfigMaps, a Secret, a PVC, a Deployment, a Service and an Ingress.

## The three decisions worth knowing before changing anything here

### 1. The Python environment is installed at boot, and it is still attested

`rapids-singlecell`, `scanpy`, `anndata`, `decoupler` and six more are not in
the RAPIDS notebooks image. The blueprint installs them with
`uv pip install -r requirements.txt` and a `mamba install` at container start,
which in a confidential deployment means the software behind an attested
manifest is whatever PyPI served that minute — the one failure the evidence
exists to make impossible.

`files/requirements.lock` is the answer: the resolved closure, every wheel named
by sha256, rendered into a ConfigMap the cloud digests and signs, and installed
with `pip --require-hashes`, which refuses anything else. The environment is
therefore named inside the deployment's own published evidence even though it is
not in the image. It costs about 30 seconds of start-up (measured, warm link,
29 wheels).

**Why not build our own image.** It would be faster to start and one digest
instead of a lock — and it would be a hand-built artifact with no CI behind it
in a repository that has never built an image, replacing a pin anybody can
verify against PyPI with one only we can reproduce. If this catalogue ever grows
image CI, that trade changes.

**Why `pip` into the image's own site-packages rather than `--target` a volume.**
`--target` puts the overlay ahead of `/opt/conda` on `sys.path`, and the lock's
closure would then shadow the CUDA builds of numpy, pandas, scipy and cupy that
cudf, cuml and cugraph are linked against. Installing in place lets pip see them
as satisfied and skip them — which is also why the lock has 29 entries and not
80. It has to happen in the main container for the same reason: an init
container's writes do not reach the container that runs afterwards.

Regenerate the lock after an image bump — the resolution depends on what the
image already has, so it must be resolved inside the image:

```bash
cd charts/rapids-singlecell-notebook
docker run --rm --user root -v "$PWD/files:/w" \
  --entrypoint bash rapidsai/notebooks:<new tag> -lc \
  'pip list --format=freeze | grep -v "@ file" > /w/image-constraints.txt
   pip install --dry-run --report /w/report.json \
     -c /w/image-constraints.txt -r /w/requirements.in'
```

then rewrite `requirements.lock` from `report.json` (`install[].metadata.name`,
`.version` and `download_info.archive_info.hashes.sha256`), and prove it:

```bash
docker run --rm --user root -v "$PWD/files:/w:ro" \
  --entrypoint bash rapidsai/notebooks:<new tag> -lc \
  'pip install --no-cache-dir --require-hashes -r /w/requirements.lock
   python -c "import rapids_singlecell, scanpy, anndata, decoupler, h5py, s3fs, clickhouse_connect"'
```

### 2. `/login` is the probe, and `/api/status` is not

`/api/status` looks like the health endpoint and jupyter_server 2.17 answers it
**403** to an unauthenticated caller — `get_login_url()` raises rather than
redirecting for an API route. A probe on it marks the pod unready for ever while
the server behind it serves perfectly. `/login` is the one route served without
a credential, and it returns 200. Measured on the image this chart pins,
2026-11-22.

The startup probe allows 15 minutes, which is the environment install plus a
cold image pull — 5 GiB compressed.

### 3. Nothing attested varies between two deployments

One hostname, in the Ingress, excluded by annotation and by the listing's
`evidence.exclude`. Everything else that differs per deployment is in the Secret,
whose values are lifted out before the snapshot is taken: the sign-in token and
the whole dataset binding. That is what makes `evidence.expectedDigest` a
property of the version, and it is checked two ways in `charts/tests/run.sh` —
`evidence_drift.py` for the hostname, and a direct diff of the bound and unbound
goldens for the binding.

## Authentication

JupyterLab's token, from a Secret, through the `JUPYTER_TOKEN` environment
variable that jupyter_server reads by itself. Never on the command line: an
argument is rendered into the manifests and published in the evidence snapshot
in clear.

The image's own command is `jupyter-lab … --NotebookApp.token=''` — no
authentication at all, which is right for a laptop and is a GPU and somebody's
patient data handed to whoever finds the hostname. The chart refuses to render an
ingress without a token.

The sign-in page is a **form** that posts the token and sets a cookie, not HTTP
Basic, so it survives a single-page console (catalogue trap 14). The listing's
`signIn` output deliberately does not hand out a `?token=…` link: a query string
lands in the gateway's access log and the browser's history.

## The GPU

`nvidia.com/gpu` request equals its limit (a cluster space sets
`maxLimitRequestRatio: 1`) and `runtimeClassName: nvidia` is set with it — a
device request without the runtime class leaves the pod Pending for ever rather
than falling back to CPU. There is one container, because Kubernetes allocates a
GPU to a container and not to a pod.

`/dev/shm` is 8Gi, memory-backed, which RAPIDS needs to talk to its Dask
workers; the 64Mi default is not enough. It counts against the container's
memory limit, so `resources.memory` includes it.

## What this chart deliberately does not do

- **No Dask scheduler or dashboard on a hostname.** The image ships
  `dask_labextension` and `jupyter_server_proxy`, so the dashboard is reachable
  through JupyterLab's own authenticated proxy. Published separately it would be
  an unauthenticated interface on a public name.
- **No multi-GPU.** One card, one pod. The blueprint's 11-million-cell
  out-of-core notebook wants "a large multi-GPU system" and is not shipped.
- **No egress restriction.** A notebook is a shell, and a tenant who can run
  `pip` can run `curl`. What the deployment guarantees is that the *platform*
  moves no data out, the gatekeeper can stop serving data to a deployment whose
  evidence changed, and nothing in the shipped notebooks reaches the network.
  It is not a data-exfiltration cage against the tenant's own operator, and the
  listing says so rather than implying otherwise.
