# Gemma 2 2B IT

An authenticated, OpenAI-compatible endpoint serving Google's Gemma 2 2B instruction-tuned, whose
deployment publishes signed evidence naming — by sha256 — the exact weight files it is allowed to
serve.

```bash
curl https://<your-hostname>/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gemma-2-2b-it","messages":[{"role":"user","content":"Hello"}]}'
```

## This model cannot do tool calling

Not "not yet", and not "off by default". Read this before you plan an agent around it.

- **Gemma 2's chat template has no `tools` handling at all.** There is nowhere for a tool
  definition to go: `apply_chat_template(tools=…)` renders a prompt in which the tools do not
  appear, so the model is never told that any function exists.
- **The engine ships no parser for Gemma 2's output format**, because there is no format — the
  model was not trained to emit tool calls. `vllm/tool_parsers` has `functiongemma` and `gemma4`,
  both for later Gemma generations, and neither applies here.

So the listing leaves `--enable-auto-tool-choice` off, and the endpoint answers a tools request
with a 400 instead of prose a client would mistake for an answer:

```
tool_choice: "auto"   → 400 '"auto" tool choice requires --enable-auto-tool-choice
                               and --tool-call-parser to be set'
tool_choice: {named}  → 400 'tool_choice="…" requires --tool-call-parser to be set'
```

Both verified on the deployed endpoint; `charts/tests/smoke/model-server.py --tools none` asserts
them, so the card's claim is a test rather than a sentence.

It would be easy to make this *look* like support — a named `tool_choice` with grammar-constrained
decoding produces well-formed JSON from any model, whether or not it was told what the function is
for. That is a syntactically valid tool call with invented arguments, and selling it as tool
calling is the dishonest option this listing declines. Use
[Llama 3.2 3B Instruct](../llama-3-2-3b-instruct/) or
[Qwen3-Coder 30B A3B (FP8)](../qwen3-coder-30b-a3b-instruct-fp8/) when you need tools.

## A system message works here, and does not upstream

Gemma 2's own chat template calls `raise_exception('System role not supported')` on a `system`
message, so an endpoint serving it unmodified answers the first request an ordinary OpenAI client
makes — or LiteLLM, or the Confidential Router — with:

```
{"error":{"message":"System role not supported","type":"BadRequestError","code":400}}
```

Verified on the deployed endpoint. The listing therefore ships a chat template with exactly one
change: a leading system message is folded into the first user turn, which is what Google's own
guidance says to do for a model with no system role. The template is in `app.yaml`, rendered into a
ConfigMap, and therefore inside the published evidence — so the modification is visible to anyone
verifying what this deployment runs, rather than hidden in an image.

## Measured, on one NVIDIA H200 NVL

An sp-vm confidential guest, driver 575.57.08, GPU in confidential-computing mode, vLLM 0.22.0 at
`--max-model-len 8192 --gpu-memory-utilization 0.85`:

| | |
|---|---|
| Ready after | 102 s (weights already on the volume) |
| Single stream | 214 tokens/s |
| 16 concurrent streams | 2672 tokens/s aggregate, 169 tokens/s each |
| GPU memory | 123 GiB claimed of the card's 140 — weights are 4.9 GiB, the rest is KV cache |
| Host memory, peak | 3.7 GiB |

8192 tokens is the model's own maximum, not a chosen limit: Gemma 2 has no long-context variant.

## Where the weights come from

`model.weights` in `app.yaml` names a repository, a commit, and every file with its size and
sha256. That block is rendered into a ConfigMap, so it is **inside the deployment's published
evidence**. An init container downloads each file, hashes it while writing, deletes anything the
manifest does not name, and refuses to let the engine start on any mismatch.

The source is `unsloth/gemma-2-2b-it`, a mirror, rather than `google/gemma-2-2b-it`. The mirror
cannot substitute bytes — every file is pinned by sha256 at a fixed commit. What the pin does not
prove is that those bytes equal Google's original, because the official repository is gated and
this listing is built without a Hugging Face token. Give CI a token with the Gemma terms accepted
and `scripts/model-weights-manifest.py google/gemma-2-2b-it` regenerates the block against the
official repository.

## What it deliberately does not do

- **No tool calling** (above).
- **No console, no accounts, no metering.** One model behind one key; the Confidential Router is
  where keys, credit and history live.
- **No `/metrics`, `/docs` or `/health` on the public hostname.** The engine authenticates `/v1`
  and nothing else, so the ingress publishes `/v1` and nothing else.
- **No CPU fallback**, and **no second replica**.

## Registering it with a Confidential Router

Copy the **connection link** from the deployment's outputs into the router's admin section, *Add
external model* — format in [`docs/model-connection-link.md`](../../docs/model-connection-link.md).
The router will serve this model for chat; it should not be offered to a caller that needs tools.
