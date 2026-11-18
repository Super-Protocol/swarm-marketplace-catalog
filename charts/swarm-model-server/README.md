# swarm-model-server

One authenticated, OpenAI-compatible model endpoint — or several on one GPU — served by vLLM from
weights pinned by sha256.

Four listings deploy this chart — [Llama 3.2 3B Instruct](../../apps/llama-3-2-3b-instruct/),
[Gemma 2 2B IT](../../apps/gemma-2-2b-it/),
[Qwen3-Coder 30B A3B (FP8)](../../apps/qwen3-coder-30b-a3b-instruct-fp8/) and
[Qwen3-Coder-Next (FP8)](../../apps/qwen3-coder-next-fp8/) — and differ only in values.

It is also a **component**: cluster-internal, no public name of its own, for a listing that needs an
LLM underneath something else. That use has its own document —
[`docs/open-inference-component.md`](../../docs/open-inference-component.md) — covering how a listing
composes it, how "which model" becomes a question put to a person, the `resources.min` arithmetic
with a worked example, and the measured performance.

## One chart, three listings

The alternative was a chart per model. The three deployments differ in an image tag's worth of
values: the weights manifest, four engine flags, and three resource numbers. Everything with any
behaviour in it is shared — the ingress class, the `/v1`-only path list, the explicit
`nvidia.com/gpu: "0"` on the init container, the refusal to publish without a key, the startup
probe's patience. A chart per model would mean finding each of those bugs three times and bumping
three append-only chart versions to fix it once.

Three *listings* either way: the marketplace card is the product, and "Llama 3.2 3B" and
"Qwen3-Coder" are not one product with a dropdown.

## What it does that a plain `helm install vllm` does not

**It refuses to serve weights it cannot verify.** `model.weights` is a manifest — repository,
commit, and every file with its size and sha256 — and the chart will not render without it. The
manifest and the fetcher that enforces it are both rendered into a ConfigMap, so both are inside
what the cloud digests and signs: the published evidence names the bytes this deployment was
permitted to serve *and* the rule that admitted them. The init container downloads each file,
hashes it while writing, removes anything the manifest does not name, and fails closed. The engine
then runs with `HF_HUB_OFFLINE=1`, so a missing or renamed file cannot be quietly fetched
unverified.

Build a manifest with `scripts/model-weights-manifest.py <repo>`. It is cheap: Hugging Face's tree
API reports each LFS file's `lfs.oid`, which *is* its sha256, so 29 GiB of weights are pinned
without downloading them.

**It refuses to publish an unauthenticated inference endpoint.** `apiKey` is required when the
ingress is on. The key reaches the container as `VLLM_API_KEY` from a Secret, never as
`--api-key` — a command line is rendered into the manifests and published in the evidence snapshot
in clear (marketplace trap 4).

**It publishes `/v1` and nothing else.** vLLM's authentication middleware guards `/v1`, `/v2`,
`/inference` and `/cohere`; `/health`, `/metrics`, `/docs` and `/tokenize` are open to whoever
reaches them. A wider ingress would put an interactive API explorer and a metrics endpoint on a
public hostname next to the authenticated one. With several engines on one hostname the same
sentence holds a segment deeper: each one publishes `/<name>/v1` and the engine authenticates it,
while `/<name>/metrics` stays unpublished.

**It refuses the misconfigurations that deploy cleanly and fail in the cluster.** Each of these is
a render error with a sentence explaining itself: no key with the ingress on, no hostname, a
revision that is a branch name rather than a commit, a file without a sha256, a `totalBytes` that
does not match the files, a volume too small for the weights, a tool-call parser vLLM does not
register, `gpu.enabled: false` (the engine's image is a CUDA build and does not serve on a CPU), an
unpinned image, a model id with characters that would break the connection link, an ingress path
outside `/v1`, and — for a shared card — fractions of its memory that add up to more than it has,
an engine with no share or no context length of its own, two engines on one port, under one name
or serving one model id, and tensor parallelism across cards that engines are supposed to be
sharing. With a preset: a slug the chart carries no model for, a preset whose licence does not
record that commercial self-hosting is permitted, and any of the five fields a preset owns set
beside it.

## The component shape

`ingress.enabled: false` and no hostname: a `ClusterIP` Service and nothing published. The key is
still demanded on every `/v1` request — a namespace is not a trust boundary once a second workload is
in it — and `persistence.size` left empty is derived from the weights manifest, which is what a
listing whose model is a *parameter* needs, since the right volume for a 2B model is not the right
volume for a 30B one.

The shape has one property the published one cannot have: with no hostname in any object, two
deployments of a version differ in the namespace alone, and the platform excludes that by default.
There is nothing for the composing listing to declare in `evidence.exclude` on this component's
behalf.

## Which model is a preset, not a value

`model.preset: <slug>` names a file in [`files/models/`](files/models/), and with it come the model
id, the weights manifest, the tool-call parser, the chat template, the quantisation — and the licence
the weights are served under, which is checked to permit commercial self-hosting and is rendered into
the attested ConfigMap as `model-licence.json`.

It exists for the shape a composing listing needs. "Which model" is a question put to a person, so
the answer arrives as a parameter's value; without presets a listing offering three models would
carry three 200-line weights manifests inside its `patches`, and nothing but a reviewer's attention
would stop a fourth option naming weights nobody had read the licence of. With presets the mapping is
one `op: replace` on `model.preset`, and an answer this chart has no preset for is a render error
listing the ones it has.

A preset is not a partial default: setting `id`, `weights`, `quantization`, `toolCalling` or
`chatTemplateFile` beside one is refused, because which side won would otherwise be a property of
Helm's merge semantics. What the listing still chooses is the context length, the share of the card,
the container's resources and the volume — properties of the deployment, not of the model. Each
preset's `sizing` block is the measured answer for that model.

[`files/models/README.md`](files/models/README.md) is how one is added, and what has to be measured
first. The four single-model listings predate this and pin their weights themselves;
`charts/tests/model_presets.py` checks that a preset and the listing for the same model still pin the
same bytes.

## Several models on one card

`model:` serves one model with the card to itself, which is what the three listings above deploy.
`engines:` serves several from the one `nvidia.com/gpu: 1` the pod holds. Setting both is refused;
the single-model render is unchanged by the existence of the other shape, down to the object it
produces.

```yaml
engines:
  - name: coder
    model: { id: qwen3-coder-next, maxModelLen: 65536, gpuMemoryUtilization: 0.6, weights: { … } }
  - name: chat
    model: { id: qwen3.8-27b, maxModelLen: 65536, gpuMemoryUtilization: 0.3, weights: { … } }
```

serves `https://<hostname>/coder/v1` and `https://<hostname>/chat/v1`, both behind the same key,
from one pod with one card in it.

**Why one container and not two.** Kubernetes allocates a GPU to a *container*, not to a pod: there
is no way for two containers to share one `nvidia.com/gpu: 1`, and the second one asking for it
leaves the pod Pending for ever. So the engines share the container that holds the device, and
`files/run-engines.py` supervises them — it starts exactly the command lines the attested
`engines.json` names, labels each engine's output, forwards `SIGTERM` to all of them, and exits as
soon as any one of them does. That last part is the point: a pod that answers for one model and
refuses connections for the other, with its restart count at zero, is an outage that reads as "the
model is broken" for a week. Readiness means *every* engine, which is why the probes exec
`files/engine-health.py` instead of asking one port over HTTP.

**Why path prefixes and not a second hostname.** Each engine is started with `--root-path /<name>`
and published at `/<name>/v1`, on the deployment's single hostname. Nothing rewrites the path: the
engine strips its own root path before comparing the request against the prefixes it authenticates
(`vllm/entrypoints/openai/server_utils.py`), and Starlette strips it again before matching a route
— so `/coder/v1/chat/completions` is both routed *and* guarded, and `/coder/metrics` is as
unpublished as `/metrics` is. That is measured in `charts/tests/root_path_guard.py` rather than
assumed, because an engine that routed the prefix without guarding it would publish an
unauthenticated inference endpoint and nothing rendered would say so. One hostname also keeps the
evidence story as it was: one DNS name, one certificate, one `/spec/rules/0/host` to exclude. And
the base URL stays an ordinary OpenAI `base_url`, so a connection link —
[`docs/model-connection-link.md`](../../docs/model-connection-link.md), whose grammar already allows
a path — needs no new field and the router needs no new parser.

**The memory is a budget, and the chart adds it up.** `--gpu-memory-utilization` is a fraction of
the card's *total* memory, so engines sharing a card fit exactly when their fractions do. Each
entry has to state its own fraction and its own `maxModelLen` — no defaults, because a share nobody
chose is a share that collides with the other engine's — and a render whose fractions exceed 0.95
is refused with the sum in the message. The last 0.05 is not spare: the driver's own allocation and
whatever a second CUDA context costs over the first live there, and an engine that finds less free
memory than it was promised refuses to start *after* downloading its weights.

**What is one per pod, not one per engine.** `resources` is the container all the engines run in,
so it covers all of them; `persistence.size` holds every manifest's bytes; `shm` and the cache
volume are shared; and one `apiKey` authenticates every engine, because this is one endpoint
serving several models rather than several endpoints. The weights volume gets a directory per
engine (`/models/<name>`), which is what keeps each fetcher's pruning inside its own model.

**What it does not do.** It does not divide the card for isolation — there is none; two engines in
one address space can starve each other for compute, and an engine that overshoots its fraction
takes the other down with it. The alternative considered and rejected was device-plugin
time-slicing (`sharing.timeSlicing.replicas`), which advertises N logical devices and would let two
*deployments* schedule on one card: no memory isolation either, no render-time budget at all, and
unverified behaviour inside the confidential sp-vm guest. The decision and its reasoning are on
SUP-332.

## Chat templates are files here, not values

A model whose own chat template cannot answer an OpenAI-shaped request needs an override — Gemma 2
is the one case here, because its template calls `raise_exception` on a `system` message. The
override is a **file in `files/chat-templates/`**, named by the listing through
`model.chatTemplateFile`, never a template passed as a value.

That is not a style preference. A chat template is Jinja, and the marketplace parses every
`{{ … }}` in a listing's values as its own expression language — seven namespaces, a dotted path,
one optional `| json`. An inlined template passes the JSON Schema, renders perfectly, and is
refused at publish with one error per tag. `.Files.Get` returns a file's bytes without rendering
them as a Helm template, so the file passes both interpolators untouched and nothing has to escape
a brace — which is the trap behind the trap, because escaping for two layers is how this gets
rediscovered. The chart refuses a template passed inline, and `apps/tests/expressions.py` refuses
one in CI.

## Weights at boot, not baked into an image

The issue that commissioned this chart leaned towards baking the weights into the image, because
that pins the model bytes into the image digest. The manifest achieves the same pin — the hashes are
in the attested snapshot, and the fetcher fails closed — without the cost, and the cost is large:

- The engine image is **27 GiB unpacked** on its own. A baked Qwen3-Coder FP8 image is ~56 GiB.
- Nothing available builds that. A GitHub-hosted runner has ~14 GiB of free disk; even with a
  disk-cleanup step it does not reach 56 GiB. Baking needs a self-hosted builder we do not have.
- Two of the three models are **gated** on Hugging Face, so baking also means redistributing
  Meta's and Google's weights from our registry. Permitted by both licences, with conditions —
  but it is a decision to take deliberately, not a side effect of a packaging choice.
- Upstream version bumps stay a one-line digest change instead of three image rebuilds.

What baking would additionally buy is no network egress from the enclave at first boot, and an
instant start after the image pull. Both are real; neither is worth a build pipeline that does not
exist. If a self-hosted builder appears, revisit — the chart would need a `model.weights.baked`
branch and nothing else. The full trade-off is tabulated in
[`docs/open-inference-component.md`](../../docs/open-inference-component.md).

### The fetcher's memory request is measured, and the obvious number was wrong

`fetcher.resources.memory` is 512Mi. It streams in 8 MiB chunks and its own resident set never
passes 40 MiB — and before SUP-839 it was OOMKilled under a 512Mi limit *and* under a 1Gi one. A
container's page cache is charged to its cgroup, the cache of a file being written stays dirty until
writeback completes, and at a few hundred MiB/s the kernel kills the writer rather than waiting. So
`release()` now hands each 64 MiB back to the kernel as it goes, and the peak is bounded by that
window rather than by the size of the file: 197 MiB for the 6 GiB Llama manifest, 100 MiB on the
restart path where the same bytes are re-hashed from the volume. `values.yaml` carries the whole
table, and `charts/tests/fetcher_guard.py` asserts the release happens — a fetcher that stopped
releasing would pass every other test here and fail in the cluster after a multi-minute download.

## The engine version is pinned for a measured reason

`appVersion` is 0.22.0, which at the time of writing is nine releases behind. `values.yaml`
documents the test matrix: newer builds either fail to import on this stand's driver or — worse —
start, pass a health check, serve at full speed and return fluent nonsense. **Re-run
`charts/tests/smoke/model-server.py` against a real card before merging a new digest.** A golden
render and a readiness probe both pass on a build that computes garbage.

## Tests

```bash
charts/tests/run.sh                 # lint, golden renders, and the refusals above
charts/tests/model_presets.py       # every preset's licence, pins, sizing and measurement
charts/tests/engine_routing.py model-server-two-engines   # one engine, one prefix, one port
charts/tests/supervisor_guard.py    # the supervisor's promises, with fake engines
charts/tests/root_path_guard.py     # a prefixed path is authenticated, not merely routed
charts/tests/fetcher_guard.py       # the refusals, and that a written file is not left in cache
charts/tests/smoke/model-server.py  # against a deployed endpoint: auth, system message,
                                    # streaming, tool choice, evidence
charts/tests/smoke/model-throughput.py
```

`model-server.py` takes the listing's **connection link** as its argument, which is the point: the
check consumes the exact artefact a person copies out of the deployment's secrets panel and pastes
into the Confidential Router, so the link format is exercised by the thing that proves the model
works. Format: [`docs/model-connection-link.md`](../../docs/model-connection-link.md).
