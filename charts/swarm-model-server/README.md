# swarm-model-server

One authenticated, OpenAI-compatible model endpoint, served by vLLM from weights pinned by sha256.

Three listings deploy this chart — [Llama 3.2 3B Instruct](../../apps/llama-3-2-3b-instruct/),
[Gemma 2 2B IT](../../apps/gemma-2-2b-it/) and
[Qwen3-Coder 30B A3B (FP8)](../../apps/qwen3-coder-30b-a3b-instruct-fp8/) — and differ only in
values.

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
public hostname next to the authenticated one.

**It refuses the misconfigurations that deploy cleanly and fail in the cluster.** Each of these is
a render error with a sentence explaining itself: no key with the ingress on, no hostname, a
revision that is a branch name rather than a commit, a file without a sha256, a `totalBytes` that
does not match the files, a volume too small for the weights, a tool-call parser vLLM does not
register, `gpu.enabled: false` (the engine's image is a CUDA build and does not serve on a CPU), an
unpinned image, a model id with characters that would break the connection link.

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
branch and nothing else.

## The engine version is pinned for a measured reason

`appVersion` is 0.22.0, which at the time of writing is nine releases behind. `values.yaml`
documents the test matrix: newer builds either fail to import on this stand's driver or — worse —
start, pass a health check, serve at full speed and return fluent nonsense. **Re-run
`charts/tests/smoke/model-server.py` against a real card before merging a new digest.** A golden
render and a readiness probe both pass on a build that computes garbage.

## Tests

```bash
charts/tests/run.sh                 # lint, golden renders, and the refusals above
charts/tests/smoke/model-server.py  # against a deployed endpoint: auth, system message,
                                    # streaming, tool choice, evidence
charts/tests/smoke/model-throughput.py
```

`model-server.py` takes the listing's **connection link** as its argument, which is the point: the
check consumes the exact artefact a person copies out of the deployment's secrets panel and pastes
into the Confidential Router, so the link format is exercised by the thing that proves the model
works. Format: [`docs/model-connection-link.md`](../../docs/model-connection-link.md).
