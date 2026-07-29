# Conversational Analyst

A conversational analyst for datasets you connect to it. A user asks a question in plain language; the agent searches a knowledge graph for the relevant tables, generates ClickHouse SQL, runs it inside the cluster space, and answers with aggregates and charts. Raw records never leave the space and are never sent to the model.

## What gets deployed

Five components, in order:

| Component | What it is |
|-----------|-----------|
| `platform` | PostgreSQL, Redis, NATS, MinIO and Neo4j. Everything stateful. |
| `identity` | Keycloak, plus a bootstrap Job that creates the agent's OIDC clients, a `demo` login and the organization that becomes its tenant. |
| `agent` | The agent itself — backend, frontend and its supporting services. |
| `gateway` | An authenticating reverse proxy in front of the agent, and the Ingress that routes `/auth` to the identity provider and everything else through the proxy. |
| `grounding` | A Job that turns the bound datasets into data-source registrations and a knowledge graph. |

## Connecting data

The `datasets` slot accepts any number of ClickHouse datasets. What you bind is what the agent can answer questions about; unbinding one removes both its registration and its part of the graph on the next reconfigure.

**There is nothing to describe at deploy time.** The agent needs to know what each table and column means, and that description already exists — it is the `schema` a dataset publisher declares on the card, including each column's `role` (`measure` or `dimension`) and its `references` to other tables. The grounding job reads it out of the binding and builds:

- a `System → Namespace → Dataset → Field` graph in Neo4j, with `REFERENCES` edges for declared foreign keys and OpenAI embeddings on every searchable node;
- one registered data source per dataset, so generated SQL has somewhere to run;
- a domain briefing in the model's system prompt, naming every source and its tables.

A dataset whose card has no schema still binds — the agent will simply not find its tables. That is a reason to describe data on the card, which is where the description belongs.

## Before you deploy

- **Hostname.** Point it at this cloud's gateway, then publish the deployment so the cloud will serve it. The identity provider's issuer is derived from this hostname, so changing it later requires a reconfigure.
- **Model keys.** A Google AI key for the model, an OpenAI key for embeddings. The embeddings key is what makes table search work; without it the grounding job fails.
- **Internal credential.** Leave it blank to have one generated. It protects the databases, the graph and the `demo` login — all of them reachable only from inside the cluster space.
- **Registry access.** The agent's images are private; the publishing organization supplies the registry credentials as publisher secrets, so a deploying user never handles them.

## Signing in

Username `demo`, password the internal credential. Add real users in Keycloak at `https://<hostname>/auth` — every user must belong to the workspace organization, or the agent has no tenant to resolve and refuses the session.
