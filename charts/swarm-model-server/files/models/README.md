# Vetted models

One file per model this chart will serve, named by the slug `model.preset` accepts. A listing that
composes this component says `model.preset: qwen3-coder-30b-a3b-instruct-fp8` and nothing else about
the model: the id, the weights manifest, the tool-call parser, the chat template and the
quantisation all come from here.

## Why the model is a preset and not a value

"Which model" is a question put to a person, and the marketplace answers it with a `select`
parameter whose value has to reach the chart. Without presets a listing offering three models would
carry three weights manifests — 200 lines each, pinned file by file — inside its `patches`, and the
only thing stopping a fourth option from naming a model nobody had read the licence of would be
whoever reviewed the pull request. With presets the listing's whole mapping is

```yaml
- op: replace
  path: model.preset
  value: "{{ params.generationModel }}"
```

and an answer this chart has no preset for is a render error with the vetted list in the message,
not a deployment.

## The licence is the gate

A preset exists only for weights whose licence permits **commercial self-hosting**, because that is
what a deployment of this component is: a tenant running somebody else's model on their own
hardware, inside an enclave, for their own purposes. `licence.commercialSelfHosting: false` is
refused at render time, and `charts/tests/model_presets.py` refuses a preset that does not record
the licence at all.

The licence is also rendered into the weights ConfigMap as `model-licence.json`, which the cloud
digests and signs — so the terms the bytes are served under are in the deployment's own evidence,
beside the sha256 of every file they apply to. A tenant can read what they agreed to out of the
artefact rather than out of a listing page.

## Adding one

1. Read the licence. Not the model card's summary of it — the licence. Record its SPDX identifier
   (or a `LicenseRef-` for the ones that have none), the URL, and every condition that survives in
   plain language. "Permits commercial self-hosting" is a statement we are making on the record.
2. Build the weights manifest:

   ```bash
   scripts/model-weights-manifest.py Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8
   ```

3. Deploy it and **measure it**, on a real card, with `charts/tests/smoke/model-server.py` and
   `charts/tests/smoke/model-throughput.py`. This is not a formality: the engine version this chart
   pins is pinned because newer builds start, pass a health check, serve at full speed and return
   fluent nonsense on this hardware (`../../values.yaml` has the matrix). A render test and a
   readiness probe both pass on a build that computes garbage, so neither of them is evidence that
   a model works.
4. Fill in `sizing` from what you measured — context length, share of the card, the container's
   resources, the smallest card the model fits on — and `measured` from the run itself. A preset
   whose numbers came from a sibling model's run is how a listing acquires a resource declaration
   nobody can reproduce.
5. `status: measured`. A preset that has only ever been rendered is `status: render-only`, and the
   listing that offers it says so where a person choosing it will see it.

## What a preset does not decide

The context length, the share of the card, the container's `resources` and `persistence.size` belong
to the deployment, not to the model — the same model serves a 8k context on a quarter of a card and
a 128k context on all of it. `sizing` is the measured recommendation for each; the listing is what
applies it.
