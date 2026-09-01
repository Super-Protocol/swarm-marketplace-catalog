# Confidential Router

An **OpenAI-compatible endpoint you can point a client at with one base-URL swap**, deployed into a
confidential cluster space, with a console for API keys, credit and generation history — and a
signed statement of what the deployment is actually running, published for anyone who wants to
check it before they send a prompt.

Pick from five small open models. Every one you pick is served under its own name and price by the
same endpoint.

```python
client = OpenAI(api_key=KEY, base_url="https://<API hostname>/v1")
```

## The attestation story

The deployment **publishes** evidence about itself: the platform signs a snapshot naming the
hardware quote and the image digests the cluster space is running, and serves it at
`/.well-known/swarm-evidence` on the API hostname. The console shows what was published and how
old it is — **published**, **stale**, or **not published**.

**Nothing here decides whether that evidence is good enough.** There is no verdict on the router's
surface, no "verified" badge, no registry of who attested it and no record that anyone did. That
question belongs to whoever is about to send a prompt, and it is answered on their machine:

```bash
gatekeeper endpoint add router --listen 127.0.0.1:8443 --upstream https://<API hostname>
gatekeeper endpoint discover router          # read what the endpoint publishes
gatekeeper endpoint trust add router --from-upstream   # pin it, after review
gatekeeper run
```

The [Gatekeeper](https://github.com/Super-Protocol/confidential-router) is a forward proxy that
runs on the client's own machine. It fetches the published bundle, checks the signature chain
against the clouds its operator trusts, matches it against the digests they pinned, and only then
lets traffic through — re-checking in the background and failing closed when a snapshot goes stale.
The router never learns whether, when, or by whom this happened. The client's base URL becomes
`http://127.0.0.1:8443/v1` and nothing else about the integration changes.

Pinning is always explicit. `--from-upstream` prints the full report and asks before it writes;
there is no trust-on-first-use anywhere in this product.

## What runs

Four components, in this order:

| Component   | What it is                                                                  | Published |
| ----------- | --------------------------------------------------------------------------- | --------- |
| `ollama`    | the model server, holding the weights you selected                          | no        |
| `litellm`   | an OpenAI-compatible face on Ollama, keyed so only the router may call it   | no        |
| `router-api`| `/v1` for clients, `/graphql` and `/auth` for the console, plus PostgreSQL  | API hostname |
| `router-ui` | the console                                                                 | console hostname |

Only the last two are reachable from outside the cluster space. An Ollama endpoint has no
authentication of its own, and publishing one would hand the deployment's GPU and models to
whoever found the name; the proxy is keyed with a credential the marketplace generates and nothing
outside the deployment ever sees.

The router meters every generation — model, token counts, cost, latency — and stores none of the
content. Prompts and completions are not written to the database and not written to the log, which
is why the console can show you a generation history and not a transcript.

## Prerequisites

- **Two hostnames**, one for the console and one for the API. Take the ones offered and the DNS is
  written for you; bring your own and point them at this cloud's gateway before publishing the
  deployment. The API hostname is the one that ends up in other people's configuration, so it is
  worth choosing deliberately.
- **An ingress controller** in the cluster space. Both ingresses name the `nginx` class; without a
  controller that serves it, the hostnames resolve and answer nothing.
- **A pull secret** for `ghcr.io/super-protocol/confidential-router/*`, which is a private package.
- **Quota** as declared: 4 CPU / 12 GB / 40 GB at minimum, 8 CPU / 24 GB / 70 GB recommended. The
  model server has no memory limit of its own and grows with the number of models kept resident.
  The declared storage covers the default volumes (30 GB of models, 8 GB of database); choosing
  larger ones is a quota decision as well as a configuration one.
- **A GPU, optionally.** Off by default, because a cluster space without one schedules nothing at
  all when it is on: the pod asks for a device and a runtime class that are not there, and waits
  forever. On CPU these models answer, slowly.

## The models

Sizes are what is pulled onto the model volume on first start. All five together are under 12 GB,
so the default 30 GB volume has room for every one of them; the memory figure is roughly what one
model needs while it is answering, and models stay resident for a day after their last request.

| Model                     | Pull size | Context | Working memory |
| ------------------------- | --------- | ------- | -------------- |
| `llama3.2:3b` *(default)* | 2.0 GB    | 128k    | ~4 GB          |
| `qwen2.5:3b`              | 1.9 GB    | 32k     | ~4 GB          |
| `gemma2:2b`               | 1.6 GB    | 8k      | ~3 GB          |
| `phi3.5:3.8b`             | 2.2 GB    | 128k    | ~5 GB          |
| `mistral:7b`              | 4.1 GB    | 32k     | ~7 GB          |

Selecting several is one field, not one switch per model: the names go to the model server, the
proxy and the router's catalogue as a single list, so the three cannot advertise different
catalogues.

## Configuring

The form is four sections, and only the first three are on the way to Deploy:

| Section | Field | Asked, or answered |
| ------- | ----- | ------------------ |
| Access | Console hostname, API hostname | Offered by the marketplace, inside a zone it holds; overwrite either to bring your own. |
| Models | Models | Defaults to `llama3.2:3b`; pick more from the five below. |
| Compute | Use a GPU, GPUs | Off, and one GPU when it is on. |
| Advanced | Model storage, Database storage | 30 GB and 8 GB, sized for all five models and an evaluation's worth of metering. |
| Advanced | First sign-in token | Generated, and shown once with the deployment's outputs. Set one to bring your own. |
| Advanced | Billing, Stripe keys, Resend key, Sender address | Manual credit. The rest appear only if you switch to Stripe. |

Nothing above Advanced has to be typed: a deployment reaches Deploy on the offered hostnames and
the default model. Advanced exists for the deployment that wants a bigger volume or real payments.

**No administrator email is asked for.** The console's first account is created for the address of
the marketplace account deploying this, read from the platform (`consumer.user.email`) rather than
retyped into a field that would drift from it.

## First sign-in

This deployment has no mailbox and no OAuth application, so the first account is not invited — it
is **claimed**. The marketplace generates a first-sign-in token, shows it once with the
deployment's outputs, and the console trades it plus your own address for the first account — the
outputs name the exact address it was created for. From that moment the endpoint that accepts it
returns 404, and everyone after the first is invited from inside the console.

## Billing

**Manual credit** is the default and is what an evaluation runs on: an administrator mints credit
from the console's Billing screen and no payment leaves the deployment.

**Stripe** takes real payments, and choosing it moves the API into production mode — where a
sign-in link written to the container log would be a sign-in link for anyone who can read logs. A
Resend API key and a sender address are therefore part of the same choice, not extras, and the
render refuses without them. Point a Stripe webhook at `https://<API hostname>/billing`, or a
completed payment never becomes credit.

## Reconfiguring

Adding a model pulls it on the next start; removing one takes it out of the catalogue and off the
volume. Hostnames, billing mode and storage sizes can all be changed by reconfiguring — a blank
sensitive field means "keep what is running", not "clear it". Changing the API hostname moves the
console with it: it is told where the API is at start-up rather than at build time, so the pinned
image never has to change for it.

Every image is pinned by digest, so what the definition says and what the cluster pulls are the
same thing, and the evidence a deployment publishes is computable from the listing before anything
is deployed.
