# Qwen3-Coder-Next (FP8)

An authenticated, OpenAI-compatible endpoint serving Qwen3-Coder-Next in Qwen's own FP8
quantisation, whose deployment publishes signed evidence naming — by sha256 — the exact weight
files it is allowed to serve.

```bash
curl https://<your-hostname>/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder-next","messages":[{"role":"user","content":"Hello"}]}'
```

**This listing needs one H200 NVL to itself.** 74.9 GiB of weights, FP8 arithmetic that only
Hopper and Ada cards do natively, and a KV budget that wants the rest of a 140 GiB card. Unlike
the [30B-A3B listing](../qwen3-coder-30b-a3b-instruct-fp8/), this one has no second card option
and does not co-exist with another model on the same GPU.

## What this model is

`Qwen/Qwen3-Coder-Next-FP8` — Qwen's own FP8 checkpoint of Qwen3-Coder-Next, ungated and
Apache-2.0. From `config.json` at the pinned revision:

| | |
|---|---|
| Architecture | `Qwen3NextForCausalLM`, `model_type: qwen3_next` |
| Parameters | 80 B total, ~3 B active — 512 experts, 10 per token, plus a shared expert |
| Layers | 48, of which **12 are full attention** and 36 are gated linear attention |
| Attention | 16 query heads, 2 KV heads, head dim 256, partial RoPE (0.25), θ = 5 000 000 |
| Context | 262 144 tokens, no RoPE scaling needed to reach it |
| Quantisation | fp8 e4m3, dynamic activation scales, 128×128 weight blocks; embeddings, router gates and the linear-attention convolutions left in bf16 |
| Weights | 74.89 GiB across 40 safetensors shards, 48 files in all |

The hybrid attention is the reason a 262 144-token default is affordable. Only the 12
full-attention layers keep a per-token KV cache: 2 KV heads × 256 dims × (K+V) × 2 bytes × 12
layers = **24 KiB per token**, against the 96 KiB per token the 30B-A3B listing pays. The other 36
layers hold a fixed-size recurrent state per sequence that does not grow with the prompt at all.

## Why it takes the whole card

| | 74.9 GiB of weights | Verdict |
|---|---|---|
| H200 NVL, 140 GiB usable | 53% of the card; ~51 GiB left for KV cache at `0.90` | **yes** |
| H100 NVL, 94 GiB | weights fit, ~10 GiB left for KV cache | no — 1.5 full-length requests |
| 80 GiB H100/A100 | weights do not fit | no |

So `resources.gpu.types` lists `NVIDIA H200 NVL` and nothing else, and `minVram` is set at 120Gi
to exclude the 94 GiB card at placement rather than at engine startup. The
`gpuMemoryUtilization` floor is `0.6` for the same reason: the weights alone are 53% of the card,
and a lower fraction cannot hold them.

## The memory budget, and what is still unmeasured

The defaults — `maxModelLen: 262144`, `gpuMemoryUtilization: 0.90` — are QA's budget from the
investigation that commissioned this listing (SUP-330), not a measurement of this listing:

- 0.90 of a 140 GiB card is ~126 GiB claimed; weights take 74.9 GiB, leaving ~51 GiB;
- at 24 KiB per token that is ~2.1 M tokens of KV cache, so roughly **eight** concurrent
  full-length 262 144-token sequences, and far more at shorter prompts.

**Not yet measured on hardware:** time to ready, throughput, and the host-memory peak. The
container's `memory` limit (48Gi) and the volume (100Gi) are derived from the 30B-A3B listing's
measured figures — 20.3 GiB cgroup peak on 29.05 GiB of weights, of which the part that scales is
the page cache the download and load pull in — scaled for a checkpoint 2.6× larger. The
deployment that measures them is a separate issue; `charts/tests/smoke/model-throughput.py` is the
check, and these numbers are what it should be held to.

## The engine is pinned to vLLM 0.22.0 deliberately

The same image digest the rest of this family pins, and not a newer one:

- v0.22.0 already registers `Qwen3NextForCausalLM`
  (`vllm/model_executor/models/registry.py`) and implements it as a hybrid model (`IsHybrid`),
  so nothing about this checkpoint needs a bump;
- it also registers the `qwen3_coder` tool-call parser this listing uses;
- every build from 0.24.0 up is measured on this hardware to either fail to import against the
  stand's CUDA 12.9 driver or — worse — start, pass a readiness probe, serve at full speed and
  return fluent wrong answers. The measurements are in
  [`charts/swarm-model-server/values.yaml`](../../charts/swarm-model-server/values.yaml).

## Tool calling

The checkpoint's own `chat_template.jinja` renders `tools` and emits Qwen3-Coder's XML tool format
(`<tool_call><function=…><parameter=…>`), which is exactly what `--tool-call-parser qwen3_coder`
reads — verified against the template at the pinned revision, the same format and parser as the
30B-A3B listing. The engine therefore runs with `--enable-auto-tool-choice --tool-call-parser
qwen3_coder`. Confirming it on a live endpoint is the deployment issue's acceptance criterion:
`charts/tests/smoke/model-server.py --tools auto`.

## Where the weights come from

`model.weights` in `app.yaml` names the repository, the commit, and all 48 files with their sizes
and sha256 hashes. That block is rendered into a ConfigMap, so it is **inside the deployment's
published evidence**: a verifier reading the evidence can see which 74.89 GiB this deployment was
permitted to serve. An init container downloads each file, hashes it while writing, deletes
anything the manifest does not name, and refuses to let the engine start on any mismatch.

The source is `Qwen/Qwen3-Coder-Next-FP8` — Qwen's own, ungated, Apache-2.0 — so there is no
mirror in the chain. Regenerate the block with
`scripts/model-weights-manifest.py Qwen/Qwen3-Coder-Next-FP8`; it reads sizes and LFS sha256 oids
from the Hugging Face tree API and hashes only the small non-LFS files, so it takes about a second
and downloads none of the 74.9 GiB. The first *deployment* does download them, and that is the
part to budget time for.

## What it deliberately does not do

- **No console, no accounts, no metering.** One model behind one key; the Confidential Router is
  where keys, credit and history live.
- **No `/metrics`, `/docs` or `/health` on the public hostname.** The engine authenticates `/v1`
  and nothing else, so the ingress publishes `/v1` and nothing else.
- **No CPU fallback, no second replica, no second card type**, and no sharing the GPU with another
  model — at 0.90 of the card there is nothing left to share.

## Registering it with a Confidential Router

Copy the **connection link** from the deployment's outputs into the router's admin section, *Add
external model* — format in [`docs/model-connection-link.md`](../../docs/model-connection-link.md).
The model id a client asks for is `qwen3-coder-next`.
