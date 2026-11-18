# The open-inference component

`charts/swarm-model-server` serves open weights through vLLM on an OpenAI-compatible API. It is
used two ways:

- as **four listings of its own** — [Llama 3.2 3B](../apps/llama-3-2-3b-instruct/),
  [Gemma 2 2B](../apps/gemma-2-2b-it/),
  [Qwen3-Coder 30B FP8](../apps/qwen3-coder-30b-a3b-instruct-fp8/) and
  [Qwen3-Coder-Next FP8](../apps/qwen3-coder-next-fp8/) — one model each, on a public hostname,
  for a tenant who wants an endpoint;
- as a **component**, cluster-internal with no public name of its own, for a listing that needs an
  LLM underneath something else.

This document is about the second use. It exists because the ported blueprint listings (SUP-837)
all need the same thing underneath — an OpenAI-compatible endpoint serving open weights, rendered
deterministically and pinned by digest — and building it three times inside three listings is how
three listings acquire three different sets of bugs.

No NIM container is involved, in either use. The reasons are in the epic: NIM's production use needs
an NVIDIA AI Enterprise entitlement, its images need a per-tenant NGC credential a listing cannot
carry, and an opaque proprietary container weakens the attestation the marketplace is selling. What
we give up by not using it is at the end of this document, with the numbers we have and an honest
note about the one we do not.

## Composing it

```yaml
components:
  - name: inference
    source:
      type: helm
      repoUrl: https://super-protocol.github.io/swarm-marketplace-catalog/charts
      chart: swarm-model-server
      version: "0.4.0"
    images:
      - name: docker.io/vllm/vllm-openai
        digest: sha256:0fec7ec5f3e6bc168e54899935fb0557da908a4832a1dbc88e2debcf2f889416
    deployment:
      values:
        base:
          # No public name of its own. The listing's own front end is what gets a
          # hostname; this answers inside the cluster space.
          ingress:
            enabled: false
          # Still authenticated. A namespace is not a trust boundary once a second
          # workload is in it, and the key rides in a Secret — never an argument.
          apiKey: ""
          model:
            preset: ""
            maxModelLen: 32768
            gpuMemoryUtilization: 0.85
          resources:
            cpu: "8"
            memory: 32Gi
        patches:
          - op: replace
            path: apiKey
            value: "{{ params.inferenceKey }}"
          # The whole of what this listing says about which model it serves.
          - op: replace
            path: model.preset
            value: "{{ params.generationModel }}"

outputs:
  - id: modelBaseUrl
    title: Model endpoint (inside the cluster space)
    type: string
    value: "http://swarm-model-server.{{ namespace }}.svc.cluster.local:8000/v1"
```

Three things are deliberate.

**`persistence.size` is absent.** Left empty, the chart sizes the weights volume from the manifest
the preset carries — every file's bytes plus a tenth for the one being written, rounded up to whole
GiB. A listing whose model is a *parameter* has no one right number to write there: the answer may
be a 2B model or a 30B one, and the largest written for all of them hands the small one a volume
four times the size of its weights for ever.

**The object names are fixed, not release-derived.** `swarm-model-server` is the Service name
whatever the release is called, which is what lets the listing's other components reach it from a
`base` value and lets an output quote it.

**The key is one parameter, used twice.** The component demands it on `/v1`; whatever calls the
component presents it. Declare it once as `generated: true, sensitive: true` and patch it into both
components — the marketplace lifts every rendered `Secret` out of the bundle and seals it, and a
generated value inside a Secret costs the evidence digest nothing (marketplace trap 5).

## Which model, as a question put to a person

The model is **not** a raw Helm value in a composing listing. It is a `select` parameter whose values
are preset slugs:

```yaml
parameters:
  - id: generationModel
    type: string
    title: Which model answers
    description: >-
      The model that writes the answers. Every option here is open-weights and licensed for
      commercial self-hosting; the licence is shown on the deployment's evidence as well as here.
    required: true
    default: qwen3-coder-30b-a3b-instruct-fp8
    options:
      source:
        type: static
        values:
          - value: llama-3.2-3b-instruct
            label: "Llama 3.2 3B Instruct — fastest, smallest card (Llama 3.2 Community Licence)"
          - value: qwen3-coder-30b-a3b-instruct-fp8
            label: "Qwen3-Coder 30B A3B FP8 — strongest, needs 80 GiB of VRAM (Apache-2.0)"
```

and the listing's whole mapping is the one `op: replace` on `model.preset` above.

What a preset answers, so that a listing does not have to:

| | |
|---|---|
| `model.id` | the name the endpoint advertises in `/v1/models` |
| `model.weights` | the repository, the commit, and every file with its size and sha256 |
| `model.quantization` | what the checkpoint already is |
| `model.toolCalling` | whether this model can call tools, and with which vLLM parser |
| `model.chatTemplateFile` | an override, where the model's own template cannot answer an OpenAI-shaped request |
| `licence` | the terms the weights are served under, **checked to permit commercial self-hosting** |

A preset and a hand-written model block are not mixed: setting any of those beside a `preset` is a
render error, because which one won would otherwise be a property of Helm's merge semantics rather
than a decision. An answer the chart has no preset for is also a render error, naming the ones it
has — which is what makes "constrained to weights whose licence permits commercial self-hosting" a
property of the chart rather than of whoever reviewed the pull request.

The licence is rendered into the weights ConfigMap as `model-licence.json`, which the cloud digests
and signs. The terms a tenant is agreeing to are therefore **in the deployment's own evidence**,
beside the sha256 of every file they apply to, rather than only on a listing page they saw once.

`charts/swarm-model-server/files/models/README.md` is how a preset is added, and what has to be
measured before it is.

### The vetted set today

| Preset | Licence | Commercial self-hosting | Weights | VRAM | Container |
|---|---|---|---|---|---|
| `llama-3.2-3b-instruct` | Llama 3.2 Community | yes, under 700M MAU, with "Built with Llama" | 6.00 GiB | 24Gi | 4 CPU / 12Gi |
| `gemma-2-2b-it` | Gemma Terms of Use | yes, subject to the Prohibited Use Policy | 4.89 GiB | 16Gi | 4 CPU / 12Gi |
| `qwen3-coder-30b-a3b-instruct-fp8` | Apache-2.0 | yes, unconditionally | 29.05 GiB | 80Gi | 8 CPU / 32Gi |

All three are `status: measured` — deployed on an H200 NVL inside an sp-vm confidential guest, not
only rendered. The numbers are below.

## How the weights reach the pod

**Decided: fetched at first boot by an init container, against a manifest that pins every file by
sha256.** Not baked into the image. The trade-off, stated:

| | Baked into the image | Fetched against a pinned manifest *(chosen)* |
|---|---|---|
| What pins the bytes | the image digest | the sha256 of every file, inside the attested ConfigMap |
| Image size | 27 GiB engine + the weights — ~56 GiB for Qwen3-Coder FP8 | 27 GiB, unchanged |
| Who can build it | nobody we have: a GitHub-hosted runner has ~14 GiB of free disk | CI as it stands |
| Redistribution | we republish Meta's and Google's weights from our registry | the tenant's deployment fetches them from the source |
| First boot | instant after the image pull | a download, then a hash of every file |
| Network egress from the enclave | none | the weights endpoint, at first boot only |
| Upstream version bump | three image rebuilds | a one-line digest change |

The attestation story is the same either way, which is the point: the manifest is rendered into a
ConfigMap, so the bytes a deployment is *permitted* to serve are named by sha256 inside its published
evidence, and the fetcher that enforces it is in the same signed document. It downloads each file,
hashes it while writing, deletes anything the manifest does not name, and refuses to mark the volume
ready on any mismatch. The engine then runs with `HF_HUB_OFFLINE=1`, so a missing or renamed file
cannot be quietly fetched unverified.

If a self-hosted builder with 60 GiB of disk ever appears, this is worth revisiting — the chart would
need a `model.weights.baked` branch and nothing else. Until then, baking costs a build pipeline that
does not exist and buys a pin we already have.

### The fetcher's memory request is measured, and the obvious number was wrong

`fetcher.resources.memory` is **512Mi**, and getting there is the most useful thing this issue
produced.

The fetcher streams in 8 MiB chunks and its own resident set never passes 40 MiB. It was still
OOMKilled under a 512Mi limit — and under a 1Gi one. A container's page cache is charged to its
cgroup; the cache of a file being *written* stays dirty until writeback completes; and at a few
hundred MiB/s the kernel kills the writer rather than waiting. Measured in a container with the
limit named, fetching the Llama 3.2 3B manifest (6.00 GiB, 9 files, largest shard 4.7 GiB, ~245
MiB/s):

| Limit | Before | After |
|---|---|---|
| 128Mi | — | survived, cgroup peak 156 MiB |
| 512Mi | **OOMKilled**, cgroup peak 515 MiB | survived, cgroup peak 197 MiB |
| 1Gi | **OOMKilled**, cgroup peak 1048 MiB | — |
| 2Gi | survived, cgroup peak 2078 MiB — the whole limit | — |

"After" is `release()` in `files/fetch-weights.py`: every 64 MiB written is synced and handed back
to the kernel with `POSIX_FADV_DONTNEED`, and every 64 MiB read while re-hashing a cached file is
dropped as it is hashed. The peak is then bounded by that window rather than by the size of the
file, which is why one number serves a 6 GiB manifest and a 31 GiB one. The restart path — the pod
comes back and re-hashes 6 GiB from the volume instead of downloading it — peaks at 100 MiB and
takes 8 s.

512Mi is 2.6x the measured peak. `charts/tests/fetcher_guard.py` asserts the release actually
happens, because a fetcher that stopped releasing would pass every other test in this repository
and fail in the cluster after a multi-minute download.

The lesson generalises past this chart: **a streaming writer's resident set is not its container's
memory demand.** The declared 2Gi it replaces was not wrong by accident — it was the number that
made the symptom go away.

## `resources.min` arithmetic

`resources.min` is the cluster space's **quota**, not a hint. It must cover every container's limits
**at once**, GPU included. Under-declared, the heaviest pod is evicted and the deployment reads as
"the app is broken" (marketplace trap 2). The rule:

```
quota = sum over pods of ( that pod's containers' limits summed,
                           or its heaviest init container, whichever is larger )
      + one surge pod of the heaviest Deployment that rolls
      + the marketplace's egress sidecar, when gated data is mounted
```

Two things are specific to this component:

- **The engine's pod does not surge.** Its `strategy` is `Recreate`, because the card is held by the
  running pod until it exits and a rolling update would leave the new pod Pending on a GPU quota of
  one for ever. So it contributes its own demand once, and nothing extra.
- **A model *parameter* means the quota is declared for the worst option the parameter allows,**
  while the volume and the engine are sized per deployment. A listing offering both Llama 3.2 3B and
  Qwen3-Coder 30B declares 32Gi of container memory and 80Gi of VRAM, because that is what the
  largest answer needs; a tenant who picks the small model simply does not use the room. The
  alternative — a quota sized for the small model — is a deployment that is refused or evicted the
  first time somebody picks the big one.

### Worked example: this component plus a retrieval stack

A listing composing the open-inference component with an embedding server, a vector store and its
own API and front end — the shape the Confidential RAG pilot (SUP-840) will have — with the model
offered as a parameter whose largest option is `qwen3-coder-30b-a3b-instruct-fp8`:

| Pod | CPU | Memory | Volume |
|---|---|---|---|
| `inference` — vLLM engine, 1x `nvidia.com/gpu` | 8 | 32Gi | 32Gi (derived: 29.05 GiB + a tenth) |
| `inference` — weights fetcher (init, sequential, not summed) | *2* | *512Mi* | — |
| `embeddings` — CPU embedding model | 2 | 4Gi | 4Gi |
| `embeddings` — weights fetcher (init) | *1* | *512Mi* | — |
| `vectors` — PostgreSQL with pgvector | 1 | 4Gi | 20Gi |
| `api` — retrieval and generation API | 1 | 2Gi | — |
| `front` — the published web interface | 0.25 | 0.5Gi | — |
| **sum of pods** | **12.25** | **42.5Gi** | **56Gi** |
| one surge pod of the heaviest rolling Deployment (`embeddings`) | 2 | 4Gi | — |
| marketplace egress sidecar, with a gated dataset attached | 0.25 | 0.125Gi | — |
| **total** | **14.5** | **46.6Gi** | **56Gi** |

```yaml
resources:
  min:
    cpu: "15"
    memory: "48Gi"
    # Not the 56Gi sum: a storage quota equal to it fails at the last volume
    # rather than the first, which reads as one broken service rather than as a
    # quota.
    storage: "64Gi"
  # The heaviest SINGLE pod, which is a different quantity from the quota and the
  # one a node has to be able to hold. Without it the cloud assumes the whole
  # 15/48Gi lands on one pod and refuses the listing on any cloud whose nodes are
  # smaller than its entire quota (SUP-824).
  maxPod:
    cpu: "8"
    memory: "32Gi"
  recommended:
    cpu: "20"
    memory: "64Gi"
    storage: "96Gi"
  gpu:
    required: true
    minCount: 1
    # The largest option the model parameter allows, not the default one.
    minVram: "80Gi"
    types:
      - NVIDIA H200 NVL
      - NVIDIA H100 NVL
```

An init container is in the table in italics and **not** in the sum: init containers run
sequentially and before the regular ones, so a pod's demand is its containers' limits summed or its
heaviest init container, whichever is larger — and here the engine's 32Gi dwarfs the fetcher's
512Mi. `charts/tests/max_pod.py` checks the `maxPod` half of this against the actual render, per
pod, which is how it stays true when a chart's limits move.

## Credentials

Nothing with a key in it leaves a `Secret`. The engine's key reaches the container as
`VLLM_API_KEY` through `secretKeyRef`, never as `--api-key` — a command line is rendered into the
manifests and published in the evidence snapshot in clear (marketplace trap 4), and that includes
the command lines that moved into `engines.json` for the shared-card shape. `charts/tests/run.sh`
asserts both, on every rendered case, rather than trusting the values file.

A composing listing inherits the same rule for its own half: the key it presents to the component is
a generated, sensitive parameter patched into both components' values, and it lands in each one's
`Secret`.

## What a deployment of the component attests

The published shape excludes its hostname from the evidence snapshot, because the hostname is the
one thing that differs between two deployments of one version and nothing else would be left to make
the digest a property of the version rather than of the deployment (marketplace traps 6 and 8).

The component shape has nothing to exclude. With `ingress.enabled: false` there is no hostname in
any object, so two deployments of one version differ in the namespace alone — which the platform
drops by default. `charts/tests/run.sh` asserts that too: two renders under different namespaces
and different keys differ in nothing but the key, and the key is in a Secret the platform lifts out
before the snapshot is taken.

So a listing composing this component does not have to declare an exclusion for it. Its own front
end's hostname is a different matter, and `evidence.exclude` has to name that one in the definition
as well as in the chart.

## Performance, and the NIM question

Measured on one NVIDIA H200 NVL in an sp-vm confidential guest — driver 575.57.08, GPU in
confidential-computing mode, vLLM 0.22.0. Reproduce with `charts/tests/smoke/model-throughput.py`
against a deployed endpoint.

| | Llama 3.2 3B | Gemma 2 2B | Qwen3-Coder 30B A3B FP8 |
|---|---|---|---|
| Context | 32 768 | 8 192 | 65 536 |
| Ready after (weights on the volume) | 97 s | 102 s | 347 s |
| Single stream | 197 tok/s | 214 tok/s | 120 tok/s |
| 16 concurrent streams, aggregate | 2 451 tok/s | 2 672 tok/s | 1 476 tok/s |
| 16 concurrent streams, each | 154 tok/s | 169 tok/s | 93 tok/s |
| GPU memory claimed | 122 GiB | 123 GiB | 122 GiB |
| Host memory, peak | 3.9 GiB | 3.7 GiB | 20.3 GiB |

**The comparison against NIM is not measured, and this says so rather than guessing.** NIM bundles a
TensorRT-LLM engine tuned per GPU, and dropping it is giving that up; the honest statement is that
we do not know what it costs on this hardware, because no NIM deployment has been run on this card.
What it would take, written down so it can be done rather than argued about:

1. an NGC credential under the NVIDIA Developer Program — benchmarking is test use, which that
   programme permits, so this is a credential question and not a licence one;
2. the matching NIM container for one of the models above, on the same H200 NVL guest;
3. `charts/tests/smoke/model-throughput.py` against both endpoints, same prompts, same context
   length, same concurrency.

Until that is run, the numbers in the table are what this component delivers and the regression
against NIM is an open number. It is worth keeping in proportion: NIM is unusable for a marketplace
listing on two independent grounds that no benchmark changes — a per-tenant NGC credential a listing
cannot carry, and an opaque container inside the thing whose whole claim is that you can verify what
ran.

What the engine pin costs, by contrast, **is** measured and is the sharper constraint:
`charts/swarm-model-server/values.yaml` records nine vLLM releases tested on this card, of which
every one after 0.22.0 either fails to import or — worse — starts, passes a health check, serves at
full speed and returns fluent nonsense. A render test and a readiness probe both pass on a build
that computes garbage, which is why `charts/tests/smoke/model-server.py` on a real card is a
condition of moving that digest.
