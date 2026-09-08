---
name: swarm-marketplace-apps
description: >-
  How to develop, validate and publish an application for the Swarm Cloud Marketplace: writing an
  AppDefinition, packaging the Helm charts it deploys, declaring deployment evidence, and getting it
  into a marketplace. Use when authoring or changing a listing, debugging why a deployment renders
  or behaves wrongly, or preparing a chart for the catalog.
---

# Authoring applications for the Swarm Cloud Marketplace

An agent reading this should be able to take an application that runs on Kubernetes and turn it into
a marketplace listing somebody can deploy in three clicks — and to know, before publishing, which of
the twenty things that silently go wrong it has avoided.

Two repositories are canonical. Read them rather than trusting this file where they disagree:

| | |
|---|---|
| **Specification** | <https://github.com/Super-Protocol/swarm-marketplace-spec> — the contract: JSON Schemas in `specs/`, prose in `README.md` |
| **Catalog** | <https://github.com/Super-Protocol/swarm-marketplace-catalog> — real listings, the charts they deploy, and the CI that checks both |

---

## 1. The model in one page

A **listing** is a declaration, not a deployment. It says what an application is made of, what the
operator must choose, and what the result will attest to. It is a file — `apps/<name>/app.yaml` —
validated against the specification.

A **deployment** happens when someone picks the listing and fills in the form. The marketplace then:

1. creates a **cluster space** — one Kubernetes namespace, sized from the listing's `resources.min`;
2. resolves the parameters, generating any it was told to generate;
3. renders every component with `helm template` — never against a live cluster;
4. lifts every rendered `Secret` out of the manifests and hands it over separately, sealed for the
   target;
5. rewrites every image reference to the digest the listing declared;
6. sends the manifests to the cloud, which applies them and publishes **signed evidence** of what it
   is actually running;
7. writes the DNS the cloud asks for, when the hostname sits in a zone the marketplace holds.

Four consequences follow from that list, and most authoring mistakes are a failure to internalise one
of them:

- **The chart is an implementation detail; the parameters are the product.** A parameter is not a
  Helm value. It is a question put to a person, and the mapping into values is the listing's job.
- **Nothing is rendered against a cluster.** No `lookup`, no cluster-dependent templating.
- **Secrets belong in `Secret` objects.** That is the only place the platform protects.
- **What is rendered is what is attested.** Anything that varies between two deployments makes the
  evidence digest vary with it.

---

## 2. Anatomy of an AppDefinition

```yaml
apiVersion: swarm.cloud/v1alpha1
kind: AppDefinition

metadata:
  name: my-app                       # lowercase, hyphenated, unique in the marketplace
  title: My Application
  description: One or two sentences, shown on the card.
  version: "1.0.0"                   # SemVer; CI refuses a changed definition with an unchanged version
  category: data-streaming           # free-form slug, rendered as "Data streaming"
  sourceRepo: https://github.com/example/my-app   # what an auditor should read
  license: Apache-2.0                # SPDX id, or LicenseRef-… when none exists
  tags: [kafka, streaming]

ui:
  sections:
    - id: access
      title: Access
      description: Optional helper text under the heading.
      fields:
        - parameterId: hostname
          widget: hostname
        - parameterId: adminPassword
          widget: password
        - parameterId: gpuCount
          widget: number
          visible: "params.gpuEnabled == true"

parameters:
  - id: hostname
    type: string
    title: Hostname
    description: Where the application is served.
    required: true
    validation:
      pattern: "^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"

  - id: adminPassword
    type: string
    title: "Administrator password (user: admin)"
    sensitive: true
    generated: true

  - id: gpuEnabled
    type: boolean
    title: Use a GPU
    description: Off by default — a space without one schedules nothing at all when it is on.
    default: false

  - id: gpuCount
    type: integer
    title: GPUs
    default: 1
    validation: { min: 1, max: 8 }

resources:
  min:          { cpu: "2", memory: "8Gi", storage: "20Gi" }
  recommended:  { cpu: "4", memory: "16Gi", storage: "50Gi" }

components:
  - name: app
    source:
      type: helm
      repoUrl: https://super-protocol.github.io/swarm-marketplace-catalog/charts
      chart: my-app
      version: "0.1.0"
    images:
      - name: ghcr.io/example/my-app
        digest: sha256:3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea
    deployment:
      values:
        base:
          hostname: "{{ params.hostname }}"
          admin:
            password: "{{ params.adminPassword }}"
        patches:
          - op: replace
            path: gpu.enabled
            value: true
            when: "params.gpuEnabled == true"

outputs:
  - id: url
    title: Open the application
    type: url
    value: "https://{{ params.hostname }}"
  - id: adminPassword
    title: Password for admin
    type: secret
    value: "{{ params.adminPassword }}"
```

### Parameters

| Field | Notes |
|---|---|
| `type` | `string`, `number`, `integer`, `boolean`, `array` (with `items`) |
| `sensitive` | Sealed for the target, masked in the UI, **stripped from evidence**, and only ever surfaced through an output of `type: secret` |
| `generated` | Filled with a random value when left blank. Only valid for a sensitive string |
| `immutable` | Cannot be changed on reconfigure |
| `options` | Required for `array`; makes a scalar render as `select` |

Two behaviours worth knowing before you design a form:

- **A blank sensitive field on reconfigure means "keep what is running"** — not "clear" and not
  "regenerate". Regenerating a credential the databases were initialised with would take the
  deployment down, so the platform refuses to.
- **`generated` is a promise that the operator never has to see the value.** If a generated value is
  also how a person signs in, surface it through an output of `type: secret`, or the deployment is
  unusable.

### Widgets

`text`, `textarea`, `number`, `select`, `multiselect`, `switch`, `slider`, `password`, `hostname`.

`hostname` is the one that does something invisible: it tells the marketplace *which* string is the
address the deployment answers on. A marketplace holding a DNS zone then offers a generated name and
writes the TXT and CNAME records itself, instead of asking the operator to invent a name and prove
control of a domain. Declare it on every address the deployment serves — a console and an API are two.

### Expressions

Available namespaces: `params.<id>`, `components.<name>.release`, `namespace`, `data.<slot>`,
`secrets.<name>`, `consumer.organization.{name,slug}`, `consumer.user.{name,email}` — plus
`binding.<field>` inside credential-provisioner environments only. Each has a scope: `visible` and a
patch's `when` may read `params` alone, and the specification's §7.3 is the authority on the rest.

`consumer` is how an application names its first tenant or administrator without asking the deploying
user to retype what the marketplace already knows.

### Resources are a quota, not a hint

`resources.min` becomes the **cluster space quota**. It has to cover every container's limits at
once, including sidecars and one-shot jobs. Under-declare it and the heaviest pod is evicted, which
presents as "the application is broken" rather than "the quota is too small".

---

## 3. The chart

Prefer an upstream chart. Vendor or author one when the upstream cannot be driven into shape from
values alone — and note that Confluent, Elastic and others ship *operators* rather than charts, which
a single namespace cannot install.

Rules that bite, in the order they usually bite:

**Name the ingress class.** Without `ingressClassName`, the object is created, the cloud registers
the hostname, the DNS is written, and nothing serves it. It looks like a DNS problem for an hour.

```yaml
spec:
  ingressClassName: nginx
```

**Pin images to immutable tags, never `latest` or a branch.** A moving tag leaves the manifests — and
therefore the attested digest — unchanged while the software behind them is replaced, which is
precisely what the evidence exists to make impossible. Then declare each image in the component's
`images:` list with its digest: the renderer rewrites every reference to the digest and **fails
closed** on any image not declared.

**Put every secret in a `Secret`.** The platform lifts all of them out of the manifests and seals
them for the target. A password in a container `env` value or a command-line argument travels in the
bundle in clear and lands in the evidence snapshot. A `Secret` is also the only place where a
non-deterministic value is safe (see §5).

**Jobs are ordinary objects here.** There is no Helm release lifecycle: install hooks are applied as
plain manifests, test hooks are dropped. A Job's `spec.template` is immutable, so the cloud deletes
and re-creates Jobs on reconfigure. Write them idempotently — they will run more than once.

**A published cluster space is frozen.** No `exec`, no `port-forward`, no writes. Anything that needs
a shell has to happen before publishing, or be built into a workload.

**Never publish an unauthenticated interface.** A hostname is public. If the upstream has no
authentication of its own — a message broker, an inference endpoint, a metrics store — keep it inside
the space and publish only what authenticates.

**Authenticating a browser UI: use a login form, not HTTP Basic.** A single-page console fetches its
manifest and icons in a mode where the browser attaches no credentials, so a Basic challenge on those
is a question the browser cannot answer: it prompts, retries anonymously, and prompts again forever.
Putting the challenge on the ingress does not help either — ingress-nginx clears the `Authorization`
header when it performs basic auth, so the application behind it then authenticates nobody. What
works is a proxy in front of the workload that collects the credential once with a form and carries
it in a session cookie.

---

## 4. Data slots and gated data

An application that consumes marketplace datasets declares **slots**, never dataset names:

```yaml
data:
  slots:
    - name: corpus
      title: Knowledge base
      required: true
      multiple: true
      accepts:
        - interface: clickhouse-http/v1
        - interface: s3-parquet/v1
      schema:
        tables:
          - columns: [{ name: text, type: string }]
```

At configure time the operator plugs in datasets that satisfy the interface and the schema; the
resolved connection reaches the values through `data.<slot>`. Credentials are never embedded in a
definition — they come from the dataset owner's vault at render time.

A dataset whose owner does not hand out credentials is served through a **gatekeeper**: the
application is given an address it can only reach the data through, and every request is admitted
against the deployment evidence the application published. That is what makes the next section
load-bearing rather than decorative.

---

## 5. Deployment evidence

After deployment the cloud digests the rendered objects, canonicalised, and signs the result. A
listing may declare in advance what that digest will be:

```yaml
evidence:
  expectedDigest: "a6e39b9f…"      # 64 lowercase hex, from a real deployment
  exclude:
    - match: { kind: Ingress, name: my-app-app-console }
      fields: ["/spec/rules/0/host"]
```

**Making the digest a property of the version.** By default a digest over a running namespace differs
for every deployment. The platform already strips the volatile parts — `/metadata/namespace`, its own
`swarm.cloud/app-deployment-id` label, `/status`, timestamps — and every `Secret`'s contents, since
Secrets are lifted out. What remains is usually one thing: **the hostname the operator chose**.
Exclude it and two deployments render an identical snapshot.

Two ways to declare an exclusion, and they produce the same result:

- `swarm.io/exclude-evidence-fields: "/spec/rules/0/host"` as an annotation on the object — preferred
  when the chart is yours;
- `evidence.exclude` in the definition — necessary when the chart is a third party's and cannot be
  annotated, and **also** what the marketplace reads to show the exclusion on the listing page. A
  digest displayed beside "no exclusions" is a misleading answer to the only question that matters
  about it, so declaring it in both places is reasonable.

**Getting `expectedDigest` takes two deployments.** It cannot be derived from the charts — a cluster
defaults fields no template writes. Deploy the version, read the evidence its deployment published,
declare that value. Adding the block does not change the rendered manifests, so a digest measured on
the version without it is valid for the version with it.

`cli/evidence-preview.js <app> [datasets…]` renders the application twice, as two different consumers
under two different hostnames in two different namespaces, and reports every field that differed.
Those fields are the candidates for exclusion. It cannot tell you the digest to declare; it tells you
whether the digest you declare will hold for anybody else.

**Anything non-deterministic outside a Secret destroys this.** A bcrypt salt, a random suffix, a
timestamp in an annotation — each makes every deployment attest differently. Inside a `Secret` it is
free, because Secrets are lifted out before the snapshot is taken.

---

## 6. Publishing

```
catalog/
  apps/<name>/app.yaml        the definition
  apps/<name>/README.md       what it is, what it needs, what it deliberately does not do
  charts/<name>/              charts this repository publishes
  charts/vendor/              upstream charts, vendored as subchart dependencies
  catalog.yaml               who publishes what, visibility, grants — policy, not definition
```

Charts are packaged by CI on push to `main` and served from
`https://super-protocol.github.io/swarm-marketplace-catalog/charts`. **The chart repository is
append-only**: a published version is pinned by definitions that were tested against it, so change
means a new `version:` in `Chart.yaml`, never an edit in place.

The definition checks run in CI and are worth running locally first:

```bash
apps/tests/run.sh      # JSON Schema against the pinned spec revision,
                       # patch targets exist, and a changed definition changed its version
charts/tests/run.sh    # helm lint plus golden renders
```

`apps/tests/run.sh` pins a specification revision deliberately (`SPEC_REF`). If a definition needs a
field the published spec does not describe yet, the honest order is: land it in the spec repository,
move the pin, then use it. There is a `KNOWN_INVALID` list for listings that predate strict
validation; adding a new listing to it is not a fix.

Filling a marketplace from the repository:

```bash
SWM_SEED_CATALOG=./swarm-marketplace-catalog node cli/seed.js
```

The seeder is re-runnable for applications — a listing already published at the same version is left
alone — but it rewrites datasets and grants unconditionally. On a shared stand, seed a one-app slice
(a `catalog.yaml` naming only the organization and the app, with `datasets: []`) rather than the whole
repository.

**A reconfigure re-renders the version the deployment was created from.** Publishing a new listing
version does not reach existing deployments; they have to be redeployed.

---

## 7. Validate before you publish

In rough order of how much time each one saves:

1. **`helm template` with the values the definition renders.** Not the chart's defaults — the values
   the listing actually produces.
2. **Render as two different consumers** and diff (§5). Anything that differs is either an exclusion
   or a bug.
3. **Run the workload.** `docker run` or `docker compose` with the same environment the chart sets is
   enough to catch the class of failure a template cannot: a broker that refuses to start under an
   authorizer, a CLI that exits 0 on a rejected statement, an image with no shell.
4. **Check the object list, not just that rendering succeeded.** Count the manifests, read the
   Ingress, confirm every image was rewritten to a digest.
5. **Deploy it once.** Nothing above proves that a hostname resolves, that a probe passes, or that a
   console loads in a browser.

Two habits that repeatedly turn out to matter:

- **Verify effects, not exit codes.** Some CLIs (`ksql --file`, for one) return 0 whether the server
  accepted the statement or rejected it. A job that reports success without doing anything is worse
  than one that fails.
- **When a fix is "obvious", check it against the thing it will actually run on.** Behaviour verified
  on a plain nginx does not predict ingress-nginx; behaviour verified with credentials in a URL does
  not predict a browser dialog.

---

## 8. Worked examples in the catalog

| Listing | What to read it for |
|---|---|
| `apps/ollama-webui` | The simplest complete listing: parameters, ingress, pinned images, outputs |
| `apps/confluent-platform` | An authored chart for software that ships only an operator; SASL accounts and ACLs; a login form in front of a console; a declared evidence digest |
| `apps/confidential-claims-fraud` | Two components composed into one deployment; per-party credentials; continuous SQL; `charts/claims-fraud-feed/README.md` explains the mechanism with diagrams |
| `apps/conversational-analyst` | Five components, data slots, publisher secrets, and a grounding job derived from the dataset's own schema |
| `apps/rag-agent` | Data slots with a schema constraint |

---

## 9. Checklist

Before opening a pull request:

- [ ] `metadata.version` bumped; the chart version too if the chart changed
- [ ] Every image pinned to an immutable tag **and** declared in `images:` with its digest
- [ ] `resources.min` covers the sum of container limits
- [ ] Every Ingress names its class
- [ ] Nothing secret outside a `Secret`
- [ ] Nothing non-deterministic outside a `Secret`
- [ ] Nothing unauthenticated published on a hostname
- [ ] Sensitive values that a person needs are surfaced as outputs of `type: secret`
- [ ] `apps/tests/run.sh` and `charts/tests/run.sh` pass
- [ ] Rendered as two consumers; the only differences are declared exclusions
- [ ] README says what the listing deliberately does not do
