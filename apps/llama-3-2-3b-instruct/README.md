# Llama 3.2 3B Instruct

An authenticated, OpenAI-compatible endpoint serving Meta's Llama 3.2 3B Instruct, whose deployment
publishes signed evidence naming — by sha256 — the exact weight files it is allowed to serve.

Built with Llama.

```bash
curl https://<your-hostname>/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"llama-3.2-3b-instruct","messages":[{"role":"user","content":"Hello"}]}'
```

## What it is for

A small, fast, tool-capable model. 3.2 billion parameters in bfloat16 is 6 GiB of weights, so most
of any modern accelerator is left for the KV cache — which is why one card serves a lot of
concurrent requests rather than one at a time.

Tool calling is the point of this listing rather than a feature of it: the engine runs with
`--enable-auto-tool-choice --tool-call-parser llama3_json`, and
`charts/tests/smoke/model-server.py --tools auto` is the acceptance check.

## Measured, on one NVIDIA H200 NVL

An sp-vm confidential guest, driver 575.57.08, GPU in confidential-computing mode, vLLM 0.22.0 at
`--max-model-len 32768 --gpu-memory-utilization 0.85`:

| | |
|---|---|
| Ready after | 97 s (weights already on the volume) |
| Single stream | 197 tokens/s |
| 16 concurrent streams | 2451 tokens/s aggregate, 154 tokens/s each |
| GPU memory | 122 GiB claimed of the card's 140 — weights are 6 GiB, the rest is KV cache |
| Host memory, peak | 3.9 GiB |

Reproduce with `charts/tests/smoke/model-throughput.py`. The numbers scale with the card: on a
smaller one the KV cache shrinks and the concurrency figure falls with it, while single-stream
decode stays roughly flat.

## Where the weights come from, and why that is not a detail

`model.weights` in `app.yaml` names a repository, a commit, and every file with its size and
sha256. That block is rendered into a ConfigMap, so it is **inside the deployment's published
evidence**: a verifier reading the evidence can see which bytes this deployment was permitted to
serve. An init container downloads each file, hashes it while writing, deletes anything the
manifest does not name, and refuses to let the engine start on any mismatch.

The source is `unsloth/Llama-3.2-3B-Instruct`, a mirror, rather than `meta-llama/Llama-3.2-3B-Instruct`.
Two honest things to say about that:

- The mirror cannot substitute bytes. Every file is pinned by sha256 at a fixed commit, so a
  compromised or changed mirror fails the init container rather than reaching the GPU.
- What the pin does **not** prove is that those bytes equal Meta's original, because the official
  repository is gated and this listing is built without a Hugging Face token. Give CI a token with
  the licence accepted and `scripts/model-weights-manifest.py meta-llama/Llama-3.2-3B-Instruct`
  regenerates the block against the official repository; nothing else changes.

Weights are fetched at first boot rather than baked into an image. See
[`charts/swarm-model-server/README.md`](../../charts/swarm-model-server/README.md) for why — the
short version is that baking a 6 GiB model into a 27 GiB engine image buys nothing the sha256
manifest does not already give, and costs a build pipeline that can push 33 GiB images.

## What it deliberately does not do

- **No console, no accounts, no metering.** This is one model behind one key. Credit, keys per user
  and generation history are the Confidential Router's job; point the router at this endpoint.
- **No `/metrics`, `/docs` or `/health` on the public hostname.** The engine authenticates `/v1`
  and nothing else, so the ingress publishes `/v1` and nothing else.
- **No CPU fallback.** The engine's published image is a CUDA build; the chart refuses to render
  without a GPU rather than deploying a pod that restarts for ever.
- **No second replica.** One card, one `ReadWriteOnce` volume.

## Registering it with a Confidential Router

Copy the **connection link** from the deployment's outputs into the router's admin section, *Add
external model*. The format is specified in [`docs/model-connection-link.md`](../../docs/model-connection-link.md);
the key is in the URL fragment, which is never sent to a server and therefore never lands in an
access log. The router fetches `https://<hostname>/.well-known/swarm-evidence`, checks the
measurement against its own trust list, pins the certificate the evidence names, and only then
proxies a request.
