# Conversational Analyst

A conversational analyst for datasets you connect to it. A user asks a question in plain
language; the agent discovers the relevant tables, writes ClickHouse SQL, runs it inside the
cluster space and answers with figures and charts.

The thing that makes this listing different from the same product installed anywhere else is
where the models are. **You supply the language model** as an OpenAI-compatible endpoint, which
may be a model server inside your own perimeter, and **the embedding model runs inside the
deployment**. Embeddings are mandatory in this product — the knowledge index and schema search
are built from them — so a deployment that sent them to a hosted provider would be sending
every table name, every column description and the contents of every document in its knowledge
base to that provider. This one does not have an address to send them to.

## What gets deployed

Six components, in order:

| Component | What it is |
|-----------|-----------|
| `platform` | PostgreSQL with pgvector, Redis, NATS and Garage (S3). Everything stateful. |
| `embeddings` | The embedding model, on CPU, from weights pinned by sha256. No Ingress: only the agent in the same namespace can reach it. Off if you bring your own endpoint. |
| `identity` | Keycloak, plus a bootstrap Job that creates the agent's OIDC clients, your administrator account, the organisations and the group tree the agent reads org-scoped roles from. |
| `agent` | The agent itself — the configuration service, the backend, the background worker that runs autopilots and reads schemas, and the UI. |
| `gateway` | An authenticating reverse proxy in front of the agent, and the Ingress that routes `/auth` to the identity provider and everything else through the proxy. |
| `grounding` | A Job that creates the model registry row, registers the bound datasets, reads their schemas into the catalogue, applies the descriptions their publishers wrote, and sets the system prompt. |

## Connecting data

The `datasets` slot accepts any number of ClickHouse datasets. What you bind is what the agent
can answer questions about; unbinding one unregisters it, which also deletes its catalogue and
its index entries — a source left behind would be working credentials for data you no longer
hold.

**There is nothing to describe at deploy time.** The agent needs to know what each table and
column means, and that description already exists: it is the `schema` a dataset publisher
declares on the card, including each column's `role` and its `references` to other tables. The
grounding job registers each source — which makes the agent read its real schema — and then
applies those descriptions as published annotations, which is what puts them in the knowledge
index the agent searches.

A dataset whose card has no schema still binds; the agent will simply have no description of
its tables to go on. That is a reason to describe data on the card, which is where the
description belongs.

## Before you deploy

- **Hostname.** Point it at this cloud's gateway, then publish the deployment so the cloud
  serves it. The identity provider's issuer is derived from this hostname, and moving it later
  costs `unpublish → reconfigure → publish` — there is no Ingress-only edit on a published
  space.
- **A model endpoint.** An OpenAI-compatible base URL ending in `/v1`, the model id it serves,
  and an access key. **Tool calling is required**; a model without it cannot work here at all.
- **Embeddings.** Leave the switch on and one is served for you. If you turn it off you must
  supply an endpoint — the field defaults to the one served here, so turning the switch off
  without changing it gives you an address that is no longer there. That is deliberate: the
  failure is loud, where the obvious alternative (an empty address) would quietly fall back to
  a hosted provider.
- **The embedding width cannot be changed later.** The knowledge index's column is created from
  it and cannot be re-sized once the index holds anything.
- **Registry access.** The agent's images are private; the publishing organisation supplies the
  registry credentials as publisher secrets, so a deploying user never handles them.

## Signing in

As the address of whoever deployed it, with the internal credential as the password — which the
deployment shows you under **Secret outputs** behind the key icon on its row, because the
default is to generate it and otherwise nobody would ever see it.

That account administers three things: the Keycloak realm, its own organisation, and the
installation (the model registry, the data sources and the schema). Add people in Keycloak at
`https://<hostname>/auth`. Everyone must be a member of an organisation, or the agent has no
tenant to resolve and refuses the session; to make somebody an administrator of an
organisation, put them in `/org/<organisation id>/admins`, which the bootstrap creates for each
one.

## What this listing deliberately does not do

- **No external model or embedding provider, at all.** There is no parameter that can point this
  deployment at one, and the two publisher-held keys earlier versions carried — a Google AI key
  for the model and an OpenAI key for embeddings and voice — are gone from the definition.
- **No speech to text.** It is optional in the product and its only provider is OpenAI's, so it
  would be a second outbound channel for a feature nothing here needs. Off by being absent from
  the configuration, which is readable in the deployment's own signed evidence.
- **Nothing masked.** The product can mark a column so its values never reach the model, and
  this listing marks nothing. That mode answers "we cannot trust the model's host", and the
  answer here is that the model's host is yours — so marking a column would cost real
  function (arbitrary SQL, preview, schema reads and statistics are refused for the whole
  source containing a marked column, and the agent stops reading uploaded files) to solve a
  problem this deployment does not have. Marking one later is an administrator's decision, in
  the product, not a change to this listing.
- **No tracing.** LangSmith is a cloud service and Langfuse would be an address outside the
  perimeter.
- **No autopilot webhook from outside.** The product can expose a token-gated trigger that
  bypasses the authenticating proxy; nothing here is triggered from outside the deployment, so
  that Ingress is not created.
- **No cluster-scoped objects, and no license.** The deployment holds no license and runs
  licensing in `report-only`, which keeps the agent's features on and still reports the state
  truthfully. A cluster space is one namespace, so the ClusterRole the licence's cluster-UID
  read would need is not something it could carry anyway.
- **It does not say anything about install administrators.** Who may administer this
  installation is a row in the platform's own database, not a manifest, so the signed evidence
  cannot attest to it. Do not read the evidence as a statement about that.

## What a verifier can read out of the signed evidence

This is the part worth knowing if you are deciding whether to trust a deployment of this
listing rather than how to run one. The snapshot a deployment publishes at
`/.well-known/swarm-evidence` carries, attested:

- every image, by digest, and the sha256 of every embedding-weight file the deployment was
  permitted to load, together with the code that enforced it;
- the **model endpoint and model id**, and the **embedding endpoint, model and vector width**;
- both egress allowlists — which tool servers the agent may call and where a signal webhook may
  be sent — and whether a private address is allowed at all;
- the absence of a speech-to-text provider;
- the result thresholds, the replica counts, the commands, the ports, the volumes and the
  service topology;
- and the rule by which the configuration's credentials are filled in, so you can see which
  values were withheld and that nothing else was.

What it does not carry is the credential the platform generated, anything derived from it, and
the hostname — the gate binds a deployment's evidence to the host it fetched it from, so the
hostname is established by the channel rather than by the digest. `apps/conversational-analyst/app.yaml`
lists every exclusion with its reason.
