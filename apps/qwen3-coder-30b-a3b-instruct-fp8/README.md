# Qwen3-Coder 30B A3B Instruct (FP8)

An authenticated, OpenAI-compatible endpoint serving Qwen3-Coder 30B-A3B Instruct in Qwen's own FP8
quantisation, whose deployment publishes signed evidence naming — by sha256 — the exact weight files
it is allowed to serve.

```bash
curl https://<your-hostname>/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-coder-30b-a3b-instruct-fp8","messages":[{"role":"user","content":"Hello"}]}'
```

**This listing needs the GPU cloud.** 29 GiB of weights and FP8 arithmetic that only Hopper and Ada
cards do natively: one H200 NVL or H100 NVL, and nothing smaller.

## Which Qwen3-Coder this is, and why it is not the 480B

The issue that commissioned this listing asked for "Qwen3-Coder, quantised to fit a single H200 NVL
(141 GB)". The flagship does not fit, by any quantisation worth serving:

| | Parameters | Weights at this precision | One H200 NVL = 140 GiB usable |
|---|---|---|---|
| `Qwen3-Coder-480B-A35B-Instruct` | 480.2 B | 960 GiB in BF16 | no |
| `Qwen3-Coder-480B-A35B-Instruct-FP8` | 480.2 B | ~480 GiB | no |
| the same at 4-bit AWQ | 480.2 B | ~250 GiB | no — still 1.8× the card |
| **`Qwen3-Coder-30B-A3B-Instruct-FP8`** | **30.5 B (3.3 B active)** | **29.05 GiB** | **yes, with 85 GiB left for KV cache** |

So the model that fits is the 30B-A3B, and on 140 GiB it does not need quantising to fit at all —
BF16 would be 61 GiB. FP8 is chosen for a different reason: it halves the weights, which buys a
much larger KV cache and therefore more concurrent requests, and Hopper does FP8 natively so it is
faster rather than merely smaller. Qwen publish the FP8 checkpoint themselves (fp8 e4m3, dynamic
activation scales, 128×128 weight blocks), so this is their quantisation, not ours.

## Measured, on one NVIDIA H200 NVL

An sp-vm confidential guest, driver 575.57.08, GPU in confidential-computing mode, vLLM 0.22.0 at
`--max-model-len 65536 --gpu-memory-utilization 0.85 --quantization fp8`:

| | |
|---|---|
| Ready after | 347 s (weights already on the volume) |
| Single stream | 120 tokens/s |
| 16 concurrent streams | 1476 tokens/s aggregate, 93 tokens/s each |
| GPU memory | 122 GiB claimed of the card's 140 |
| KV cache | 934 544 tokens — 14.3 full-length requests at once at the default 65 536 |
| Host memory, peak | 20.3 GiB |

Reproduce with `charts/tests/smoke/model-throughput.py`. 347 s to ready is the honest figure for a
30 GiB mixture-of-experts checkpoint; the first boot is longer still, because the weights are
downloaded and hashed before the engine starts.

## Tool calling

Works, and is the acceptance criterion rather than a feature. The engine runs with
`--enable-auto-tool-choice --tool-call-parser qwen3_coder`, which parses Qwen3-Coder's XML tool
format. Verified on the deployed endpoint: `tool_choice: "auto"` returns a `tool_calls` entry with
`finish_reason: tool_calls`, and a named `tool_choice` works too —
`charts/tests/smoke/model-server.py --tools auto` is the check.

## Context length is a real trade

262 144 tokens is the model's own maximum; the listing defaults to 65 536. At 96 KiB of KV cache per
token, a full-length 262 144-token request is 24 GiB of cache on its own, so the longer context buys
itself at the cost of concurrency. Asking for more than the card can hold makes the engine refuse to
start rather than truncate quietly, which is the right failure but still a failure — change it in
Advanced deliberately.

## Where the weights come from

`model.weights` in `app.yaml` names the repository, the commit, and all twelve files with their
sizes and sha256 hashes. That block is rendered into a ConfigMap, so it is **inside the deployment's
published evidence**: a verifier reading the evidence can see which 29 GiB this deployment was
permitted to serve. An init container downloads each file, hashes it while writing, deletes anything
the manifest does not name, and refuses to let the engine start on any mismatch.

The source is `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8` — Qwen's own, ungated, Apache-2.0 — so unlike
the other two listings in this family there is no mirror in the chain. Regenerate the block with
`scripts/model-weights-manifest.py Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8`.

## What it deliberately does not do

- **No console, no accounts, no metering.** One model behind one key; the Confidential Router is
  where keys, credit and history live.
- **No `/metrics`, `/docs` or `/health` on the public hostname.** The engine authenticates `/v1`
  and nothing else, so the ingress publishes `/v1` and nothing else.
- **No CPU fallback**, **no second replica**, and **no smaller card**. `resources.gpu.types` lists
  H200 NVL and H100 NVL only: an A100 is compute capability 8.0 and has no native FP8, and an L40S
  has FP8 but not the memory to be worth it here.

## Registering it with a Confidential Router

Copy the **connection link** from the deployment's outputs into the router's admin section, *Add
external model* — format in [`docs/model-connection-link.md`](../../docs/model-connection-link.md).
