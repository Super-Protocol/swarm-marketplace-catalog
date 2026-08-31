# Swarm Cloud Marketplace Catalog

Ready-to-publish marketplace listings — applications, datasets and the Helm charts they deploy — written to the [Swarm Cloud Marketplace Specification](https://github.com/Super-Protocol/swarm-marketplace-spec).

The specification repository defines the *contract*. This repository holds the *content*: real definitions a marketplace can be filled from, and the charts those definitions point at.

## Layout

```
apps/<name>/app.yaml           AppDefinition + README
datasets/<name>/data.yaml      DataDefinition + README
interfaces/<name>/<version>.yaml   DataInterface contracts, mirrored from the spec repository
charts/<name>/                 Helm charts published to the chart repository below
charts/vendor/                 Upstream charts, vendored as subchart dependencies
catalog.yaml                   Listing policy: who publishes what, visibility, grants, reviews
```

`apps/`, `datasets/` and `interfaces/` are exactly the structure the specification prescribes; every file in them validates against `specs/*.schema.json` in the spec repository.

`apps/tests/run.sh` is that sentence as a check: it fetches the schemas at a pinned revision and validates every `app.yaml` against them, then checks what a schema cannot — that a patch has something to patch and that a base key reaches a value the target chart declares. `charts/tests/run.sh` does the same for the charts, by rendering them and diffing against committed goldens.

## Chart repository

The charts under `charts/` are packaged by CI and served from GitHub Pages:

```
https://super-protocol.github.io/swarm-marketplace-catalog/charts
```

That URL is what the `repoUrl` fields in `apps/*/app.yaml` refer to. Adding or changing a chart means bumping its `version:` — the published index is append-only, and a definition pins the version it was tested against.

## Filling a marketplace from this repository

```bash
git clone https://github.com/Super-Protocol/swarm-marketplace-catalog
SWM_SEED_CATALOG=./swarm-marketplace-catalog node cli/seed.js
```

The seeder reads `catalog.yaml`, publishes every definition it names through the ordinary publishing path, and applies the grants and reviews. It is re-runnable: a listing already published at the same version is left alone.

`${VAR}` placeholders in `catalog.yaml` are substituted from the environment. Nothing in this repository is a real credential.

## What is here

| Listing | Kind | Notes |
|---------|------|-------|
| `conversational-analyst` | Application | A conversational analyst over connected datasets. Five components; the grounding component turns whatever is bound into the agent's knowledge graph and data-source registrations. |
| `confidential-router` | Application | An OpenAI-compatible endpoint whose deployment publishes signed evidence of what it runs, with a console for keys, credit and generations. Four components, three charts of our own, and the multi-select model parameter. |
| `ollama-webui` | Application | Ollama with a chat UI. The simplest thing that exercises parameters, ingress and pinned images. |
| `confluent-platform` | Application | Kafka with Confluent's Control Center. A chart of our own, because Confluent's Kubernetes path needs an operator; shows a multi-container pod and generated console credentials. |
| `rag-agent` | Application | Data slots, including a schema-constrained one. |
| `roczen-metabolic-programme` | Dataset | De-identified longitudinal metabolic-programme records, 10 tables. |
| `autonomyx-inflammation-monitoring` | Dataset | De-identified wearable biometrics and AI inflammation scores, 9 tables. |
| `acme-internal-knowledge-base` | Dataset | Restricted internal corpus. |
| `retail-transactions` | Dataset | Public transactional dataset. |

## Why the dataset cards are this detailed

Every column in the clinical datasets carries a `description`, a `role` (`measure` or `dimension`) and, where it is a foreign key, a `references`. That is not documentation for humans — it is the input the `conversational-analyst` grounding job turns into a Neo4j knowledge graph and a domain briefing for the model.

A dataset published with a bare schema still deploys; the agent simply knows less about it. A dataset published with a described schema needs no second description anywhere, and cannot have one that drifts.
