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

**What the snapshot covers, and what it leaves out.** Besides the hardware quote and the image
digests, the signed snapshot carries the rendered Kubernetes objects — including `router.yaml` in
full, which is how a reviewer can see which models are served, which endpoints are attested, what
the rate limits are and whether sign-up needs an invitation. Left out are the two hostnames the
operator chose and the handful of values derived from them: the API's public base URL, the browser
origins allowed to call it, the checkout return URL, the campaign landing origin, and the API
origin the console fetches from. They live in ConfigMaps of their own, named in the listing's
`evidence.exclude` block and annotated on the objects that carry them.

Left out for a different reason, and worth saying plainly: **your own email address is not in the
snapshot.** The marketplace hands this deployment the address of the account deploying it — it is
who the first administrator account is created for and who may read the campaign numbers — and both
copies of it travel to the pod sealed, out of a Secret whose values the platform strips before it
signs anything. Until chart 0.8.0 one of the two was a plain value in the container's environment,
which published it to anyone who fetched the bundle and made the digest a property of who deployed
this listing rather than of its version (SUP-241).

That is a deliberate trade and it runs one way only: *where* this deployment answers is not
attested, *what* it is configured to do is. The reason is that a digest containing the hostname
would be a digest of one deployment — nobody else could ever reproduce it, and a pinned value would
admit exactly one endpoint. With them out — and with the deployer out — two deployments of the same
listing version attest the same snapshot, which is what makes a published digest worth comparing
against at all.

## What runs

Four components, in this order:

| Component   | What it is                                                                  | Published |
| ----------- | --------------------------------------------------------------------------- | --------- |
| `ollama`    | the model server, holding the weights you selected                          | no        |
| `litellm`   | an OpenAI-compatible face on Ollama, keyed so only the router may call it   | no        |
| `router-api`| `/v1` for clients, `/graphql` and `/auth` for the console, plus PostgreSQL and the attesting egress | API hostname |
| `router-ui` | the console                                                                 | console hostname |

Only the last two are reachable from outside the cluster space. An Ollama endpoint has no
authentication of its own, and publishing one would hand the deployment's GPU and models to
whoever found the name; the proxy is keyed with a credential the marketplace generates and nothing
outside the deployment ever sees.

The router meters every generation — model, token counts, cost, latency — and stores none of the
content. Prompts and completions are not written to the database and not written to the log, which
is why the console can show you a generation history and not a transcript.

### The database is replicated, and that is not a performance decision

A tenant volume on this platform lives on its node's state disk, which is re-keyed on every boot:
the node that reboots comes back with an empty volume. A single-instance database on it comes back
*healthy and empty*, which is the worst shape a data loss can take, and routine TCB updates reboot
nodes (SUP-179).

So `router-api` deploys three PostgreSQL instances under Patroni — one per node, hard anti-affinity,
and a commit acknowledged only once a second instance has it on disk. A node going away promotes a
standby, the master Service follows the new leader, and the returning node rebuilds its copy from
the cluster without anybody being asked to do anything. The address the API connects to does not
change, and neither does anything a client sees.

Two things it does not do. It does not survive every node rebooting at once — there would be nothing
left to re-sync from — which is why an image update of a swarm cloud has to roll one node at a time.
And it does not make a deployment whose nodes are all on one physical machine survive that machine:
replication across three cVMs on one host is not three hosts.

### Models in other deployments, attested before anything is sent to them

An administrator can register a model endpoint that lives somewhere else — another deployment of
this listing, another swarm cloud — and it appears in `/v1/models` and in the console beside the
local ones, at a price they set. Nothing is proxied to it until it has been verified.

The verification is the Gatekeeper's own core, running as a second container in the API pod. It is
the same binary a client runs on their own machine, which is the point: the check this deployment
performs on an upstream and the check a user performs on this deployment cannot drift apart,
because there is only one implementation of it. For each registered endpoint it fetches the
upstream's evidence, verifies the signature chain and the hardware report, requires the
measurement to be on a trust list the administrator maintains, binds the observed TLS leaf to the
evidence, and from then on connects to that pinned certificate only — never to the global CA
bundle. It re-verifies every ten minutes, immediately on a certificate that does not match the
pin, and immediately after an edit to the trust list.

**Fail-closed, in both halves.** A failed check drops the model out of `/v1/models` and refuses
routing to it; a request that was in flight when a verdict was withdrawn ends with
`attestation_revoked`. The API refuses before it opens a connection and the egress refuses again
one hop later — two independent refusals for one rule, in two processes.

A measurement admits a *cloud*, not a deployment: it says what kind of confidential VM answered,
not which one. That is weaker than the digest pin a user puts on this router, and it is the trade
the trust list makes deliberately — the alternative is approving every upstream deployment by hand.
The endpoint's own published evidence is fetched and shown beside the verdict, informational and
never gating.

**The upstream's API key** is whatever credential that endpoint expects, entered once at
registration and sealed with AES-256 under a key the marketplace generates and keeps in a Secret —
so the plaintext is absent from SQL, from a database dump and from the log. It is never rendered
back; rotating it is a new write. Rotating the sealing key makes every stored upstream key
unreadable, and they are entered again rather than recovered.

None of this is a chart value. Endpoints and trusted measurements are registered at run time in
the console's admin section, which is what keeps the deployment's attested snapshot the same for
everybody: two deployments of this listing version still publish the same digest, however
differently they are configured afterwards.

## Prerequisites

- **Two hostnames**, one for the console and one for the API. Take the ones offered and the DNS is
  written for you; bring your own and point them at this cloud's gateway before publishing the
  deployment. The API hostname is the one that ends up in other people's configuration, so it is
  worth choosing deliberately.
- **An ingress controller** in the cluster space. Both ingresses name the `nginx` class; without a
  controller that serves it, the hostnames resolve and answer nothing.
- **Three schedulable nodes.** The database is three instances with required one-per-node
  anti-affinity, so on a smaller cloud the extra ones stay Pending — which is the chart refusing to
  put two copies of the data on one ephemeral disk, not a fault. A single-node cloud needs
  `postgresql.replicaCount` lowered, and then it is a database that does not survive its node.
- **Nothing to authenticate against a registry.** `ghcr.io/super-protocol/confidential-router/*` is
  a public package; a cluster that can reach ghcr.io can pull it.
- **Quota** as declared, unchanged by this version: 5 CPU / 14 GB / 60 GB at minimum, 9 CPU / 26 GB / 120 GB recommended. The
  model server has no memory limit of its own and grows with the number of models kept resident.
  The declared storage covers the default volumes — 30 GB of models and **three** 8 GB database
  volumes, because the database is three instances holding a copy each; choosing larger ones is a
  quota decision as well as a configuration one, and the database size is multiplied by three.
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

The form is five sections, and only the first three are on the way to Deploy:

| Section | Field | Asked, or answered |
| ------- | ----- | ------------------ |
| Access | Console hostname, API hostname | Offered by the marketplace, inside a zone it holds; overwrite either to bring your own. |
| Models | Models | Defaults to `llama3.2:3b`; pick more from the five below. |
| Compute | Use a GPU, GPUs | Off, and one GPU when it is on. |
| Campaign | Campaign landing page, PostHog project key | Both empty. A deployment that hands out no invitation codes and measures no funnel needs neither. |
| Campaign | Only invited people can create an account | Off. An invitation then decides whether $100 comes with an account, not whether the account can exist. |
| Advanced | Model storage, Database storage | 30 GB and 8 GB, sized for all five models and an evaluation's worth of metering. The database size is per instance, and there are three. |
| Advanced | First sign-in token | Generated, and shown once with the deployment's outputs. Set one to bring your own. |
| Advanced | Allow sign-up with a password | On. It is what lets a second person in, since this deployment cannot send an invitation. |
| Advanced | Billing, Stripe keys, Resend key, Sender address | No purchases. The rest appear only if you switch to Stripe. |

Nothing above Advanced has to be typed: a deployment reaches Deploy on the offered hostnames and
the default model. Advanced exists for the deployment that wants a bigger volume or real payments.

**No administrator email is asked for.** The console's first account is created for the address of
the marketplace account deploying this, read from the platform (`consumer.user.email`) rather than
retyped into a field that would drift from it.

## Running a campaign

Invitation codes grant $100 of credit inside sign-up, so a visitor never types one: a mailing links
to the landing page with the code in the URL, the page carries it to the console, and the grant is
applied once, atomically, as the account is created. The codes themselves are minted against the
database; the two fields in *Campaign* are what a deployment configures.

**Campaign landing page** is the hostname of that page — a site hosted elsewhere, not by this
deployment. Naming it does two things, which is why it is one field rather than two that can drift:
its origin becomes the one origin besides the console allowed to call `GET /v1/invites/<code>` from
the browser, which is how the page can tell a visitor the credit is already theirs before they have
signed up; and it becomes the origin the generated invitation URLs point at. Leave it empty and no
invitation URL this deployment mints will point anywhere useful — and a page that does look a code
up gets an answer the browser throws away, with nothing in the page to say so.

**PostHog project key** is the project the funnel is recorded in. Without it every event is accepted
and dropped: sign-up, redemption and first request then exist only as rows in this deployment's own
database. It is a write-only ingest key — it can add events and read nothing back — and it is
delivered as a sealed value rather than written into the deployment's configuration, which is
published inside the evidence bundle and readable by anyone.

**Only invited people can create an account** is the switch that turns a campaign into a closed
launch. Off, this deployment's sign-up is open and a code only decides whether the $100 comes with
the account — a wrong or spent code costs the credit and never the registration. On, the code
decides whether the account exists: every sign-up path — password, magic link, an OAuth callback —
is refused, before anything is created, unless the request carries a code that is valid and has not
been redeemed. The visitor is told which of three it was: no code at all, already claimed, or
expired-or-unknown, that last one deliberately merging "withdrawn" and "never issued" so the
console cannot be used to tell a guessed code from a real one.

Two things it does not touch: signing in to an account that already exists, and the first-sign-in
token — nobody mails the operator a code for their own cluster. **Generate the campaign's codes
before turning it on.** With none issued, registration is closed to everyone, so claim the
administrator account with the token first.

**Who can read how it converts** is not asked for either. The console's operator-only queries — a
campaign's issued, redeemed, activated and granted totals — answer for the address of the
marketplace account deploying this, the same address the first administrator account is created
for. Nobody else: the list is empty on a deployment that does not name anyone, and a published
cluster space has no exec to run the `invites stats` CLI through instead. If the numbers have to be
readable by more than one person, they read them through that account.

## Signing in

This deployment has no mailbox and no OAuth application, so nobody here is ever *invited*. Two
paths replace that, and both work with nothing but the cluster:

**The first account is claimed.** The marketplace generates a first-sign-in token, shows it once
with the deployment's outputs, and the console trades it plus your own address for the
administrator account — the outputs name the exact address it was created for. From that moment
the endpoint that accepts it returns 404. Claim the deployment before you publish its hostname:
the token is spent by the *first* account, whoever creates it.

**Everyone after that signs up.** `https://<console hostname>/signup` takes an email address and a
password, and answers with a session — there is nothing to deliver and nothing to verify. Each
account arrives with its own empty workspace and no credit; it can read nobody else's keys,
generations or balance. What it does mean is that **anyone who can reach the console can create an
account** — unless *Only invited people can create an account* is on, which is what a closed launch
wants; otherwise switch *Allow sign-up with a password* off in Advanced where open registration is
not what you want. Switching it off leaves the administrator's own way back in as a magic link written to the
API log, which is where sign-in links went before this existed — or as real mail, on a Stripe
deployment, since that one configures Resend.

**Password reset needs a mail provider.** Without one there is none: an administrator can create a
fresh account, and a lost one is a lost workspace. Set one under *Mail* (next section) and the
sign-in screen offers *Forgot password?*, and a one-time sign-in link besides.

## Mail

Optional, and off by default. *Mail provider* is `None`, `SMTP` or `Resend`. With one set:

- a forgotten password is reset from the sign-in screen — the link works once, expires in an hour,
  and signs the account out everywhere else; the request answers the same whether or not the
  address has an account, and is rate-limited;
- every new account gets a welcome email (Super Protocol logo, the console address, the credit it
  started with);
- the sign-in screen also offers a one-time link mailed to the address.

**Deliverability is yours.** Mail reaches inboxes only if the sender address's domain publishes SPF
and DKIM records covering the server that sends it (for Resend, a domain verified there). That is
DNS this deployment cannot write.

**Reachability is reported.** The API checks the SMTP server at start-up without sending anything,
and every send after that updates the `mail` field of `https://<API hostname>/health`: `ok`, or
`failing` with `unreachable` (no connection from this cloud — a cluster space admits outbound
connections to public addresses only, and some clouds block port 25) or `auth_failed`.

**Nothing about it is attested.** Every mail setting — the provider included — reaches the API out
of the deployment's Secret, so two deployments of one version that differ only in their mail setup
publish the same evidence digest, and the SMTP password is in no log, configuration or snapshot.
Reconfiguring them restarts the API: the pod template carries a hash of the mail settings, excluded from the snapshot like the hostname hash.

## Billing

**No purchases** is the default and is what an evaluation runs on: the console has no buy panel, a
checkout is refused, and credit arrives as a grant — an invitation code redeemed at sign-up, the
feedback offer, or an administrator's `credits grant`.

It replaced a default called *manual credit*, which minted credit from a signed link. That provider
exists for a developer's laptop, and the API now refuses to bind it on any hostname other people can
reach: this listing's previous default shipped a working, unbounded, free-credit button to every
account holder of a public deployment (SUP-167). The chart refuses to render it and there is no form
field for it.

**Stripe** takes real payments. With no mail provider chosen, a Resend API key and a sender address
are part of the same choice, not extras, and the render refuses without them: the container runs in production mode, where a
sign-in link written to the container log would be a sign-in link for anyone who can read logs. Point
a Stripe webhook at `https://<API hostname>/billing`, or a completed payment never becomes credit.

## Reconfiguring

Adding a model pulls it on the next start; removing one takes it out of the catalogue and off the
volume. Hostnames, billing mode, storage sizes and both campaign fields can all be changed by
reconfiguring — a blank sensitive field means "keep what is running", not "clear it". Changing the API hostname moves the
console with it: it is told where the API is at start-up rather than at build time, so the pinned
image never has to change for it.

Every image is pinned by digest, so what the definition says and what the cluster pulls are the
same thing, and the evidence a deployment publishes is computable from the listing before anything
is deployed.

**Moving to 0.12.0 is a re-pin rollout.** The attesting egress is a new container in the API pod,
so this version's snapshot is not the previous version's. Anyone who pinned a digest of 0.11.0
pins both digests, deploys, and then removes the old one — the pattern ADR-003 §3 pre-approves for
exactly this. Nothing a client sends changes; the base URL, the keys and the models are the same.
