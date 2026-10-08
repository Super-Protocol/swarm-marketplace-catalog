# Chart tests

`helm lint` over the charts this repository publishes, and a rendered-output golden for
each case in [`cases.tsv`](./cases.tsv). Run them from the repository root:

```bash
charts/tests/run.sh            # lint + render + diff
UPDATE=1 charts/tests/run.sh   # rewrite the goldens after an intended change
```

A golden holds only the documents the case's own chart produced —
`# Source: <chart>/templates/…`. Subchart output is dropped: a vendored
PostgreSQL bump would otherwise rewrite every golden that installs one, and
those hundreds of lines are the vendor's contract, not this repository's.

The release name and namespace are fixed per listing (`cr` / `confidential-router`,
`cs3` / `confidential-s3`) so the output does not move with whoever ran it.

## The cases

| Case | Axis it pins |
| --- | --- |
| `api-one-model` / `api-three-models` | the `models` list: `models[]` and `endpoints[]` in the rendered `router.yaml` are built from it |
| `api-billing-disabled` / `api-billing-stripe` | the billing mode: no purchases at all, versus real card payments. The third provider, `manual`, mints credit from a signed link and the chart refuses it (SUP-167) |
| `api-no-models` | an empty selection: an empty catalogue renders, rather than a chart that cannot be installed |
| `api-external-postgres` | `postgresql.enabled: false` with a DSN of the deployment's own |
| `litellm-one-model` / `litellm-three-models` | the same list on the other side, so the two charts' model names can be diffed against each other |
| `litellm-no-models` | an empty selection: no proxy at all — one inert ConfigMap, because a marketplace component has to render something for the cloud to accept it (SUP-245) |
| `api-bootstrap-token` | the SUP-95 auth seam: the one `CR_API_*` env var, rendered only when a token is set |
| `api-password-no-mailer` | what the listing deploys (SUP-112): passwords on with `mailer: none`, which has to render a config the router boots on |
| `api-password-off` | passwords off: the `auth.password` block is absent rather than `enabled: false`, so the chart stays bootable on an image that predates the key |
| `api-invite-only` | the SUP-173 auth seam: `requireInviteForSignUp` rendered only while it is on, so the chart stays bootable on an image that predates the key |
| `api-endpoint-hostname` | an endpoint that names a hostname of its own: a parameter, so it is rendered as a literal rather than as `${ROUTER_PUBLIC_HOSTNAME}`, and the public ConfigMap does not carry that key at all (SUP-211) |
| `api-external-only` | what the listing renders by default from 0.13.0 (SUP-245): no built-in model and the egress on — a router that serves only the external endpoints an administrator registers |
| `api-external-endpoints` | the attested egress on (ADR-008): the gatekeeper as a second container, the shared `emptyDir` they talk through, and `CR_API_SECRETS_KEY` in the Secret rather than in the attested `router.yaml`. Every other `api-*` case has it off, which is the other half — a chart that still boots an image predating the key |
| `ui-default` | the console's env and ingress; run.sh renders it against a second hostname as well, because one pinned image has to serve any API origin |
| `ollama-one-model` / `ollama-gpu` | the model server and its GPU switch: an explicit zero device request without one; the device count, the `nvidia` runtime class and the GPU toleration together with one |
| `ollama-no-models` | an empty selection: no Deployment, no Service, no volume — one inert ConfigMap — with the GPU switch on, which therefore reaches nothing (SUP-245) |
| `s3-default` | what the confidential-s3 listing deploys: two hostnames, credentials derived from seeds, the bundled engine and the bundled PostgreSQL |
| `s3-virtual-host` | `server.s3_domains` in the gateway's config — the one line between an SDK that works out of the box and one that has to be told to use path style |
| `s3-external-postgres` | `postgresql.enabled: false` with a DSN of the deployment's own |
| `s3-explicit-credentials` | a real master key and a real Garage credential instead of seeds; the chart checks both shapes rather than trusting them |
| `s3-large-volumes` | the large end of the form, and the engine's layout capacity, which is derived from the data volume rather than set beside it |

The model server was otwld's `ollama` chart up to listing 0.12.x. It is
`confidential-router-ollama` from 0.13.0, because a router serving only external
endpoints has to be able to deploy no model server at all, and a component cannot
be skipped by the listing — so the chart decides from the `models` list itself
(SUP-245).

## Beyond lint and goldens

Ten checks in `run.sh` are not a golden diff, and each exists because a golden diff
cannot answer the question:

- **Every container is admissible on a cluster space** (`limitrange.py`). A cluster space
  carries a LimitRange the cloud writes itself: a 100m / 128Mi floor, and a request/limit
  ratio of 1. A container under the floor, or one declaring no resources at all and handed a
  2:1 pair by that same LimitRange, is refused at admission — so the pod never exists, and
  nothing an operator looks at says "quota". It reads as "the app is broken". Four sub-floor
  containers shipped that way before this check existed (SUP-238), and a golden diff held
  every one of their numbers without a word.

- **Every object, parsed as a cluster parses it** (`inventory.py`). A template that loses a
  `---` glues two objects into one document; the golden is regenerated from the same broken
  render so the diff is empty, and `helm lint` reads the merged document as the second object
  and finds nothing wrong. The cluster applies one and silently drops the other. That happened
  here, to the confidential-s3 gateway's Service, and it presented as an S3 endpoint answering
  503 through an Ingress pointing at nothing.

- **Every image pinned by a real digest** (`digests.py`). The cases set their image digests
  explicitly, so the goldens stay stable across an image bump — and say nothing about what the
  chart itself would pull. A listing overrides the defaults too, which leaves an empty or
  placeholder default invisible until somebody runs `helm install` with no `--set`, or until a
  chart is published to an append-only repository carrying a digest nobody can pull.
- **No consumer value in an attested manifest** (`consumer_fields.py`). The two-consumer render
  below cannot see this one: it renders the chart with fixed values, while `consumer.*` is
  resolved by the marketplace when it turns a definition into values — so the gap is the seam
  between the listing and the chart. confidential-s3 0.1.0 passed `consumer.user.email` and
  `consumer.organization.name` straight into the control plane's environment; it rendered,
  deployed and worked, and made the evidence digest a property of who deployed it. A real
  deployment is what found it.

  Run against every listing that passes one, and once per set of case values. Both halves of
  that sentence are SUP-241: the check existed and was wired to confidential-s3 alone, while
  `confidential-router` passed the deployer's address into two chart values — one sealed, one a
  literal in two containers' env lists — and a chart renders an env var only on the path that
  uses it, so a single set of values clears the paths it happens to exercise and is silent about
  the rest. A path whose marker reaches nothing is reported rather than passed; lists are walked,
  because an address passed as a one-entry list was invisible to the first version of this.
- **Two consumers, one version.** The confidential-s3 chart is rendered twice, under two
  hostnames in two namespaces, and every field that differs has to be a declared exclusion.
  This is `cli/evidence-preview.js` done on the chart, and it is the only check that catches
  a hostname leaking into a manifest outside an Ingress — which renders perfectly, deploys
  perfectly, and quietly makes every deployment of the version attest a different digest.
  It found one: the console's `S3_PUBLIC_ENDPOINT`.
- **The listing declares what the chart annotates.** An exclusion the chart applies and the
  definition does not is a digest shown beside "no exclusions", which answers the only
  question that matters about it wrongly.
- **Two consumers, one version — field by field** (`evidence_drift.py`). The same question as
  the confidential-s3 check above, asked about `confidential-router` and asked of JSON Pointers
  rather than of diff lines. The line-based version passes as long as no unexpected *string*
  appears, which cannot see a field that differs by being absent on one side, cannot tell an
  excluded field from one that merely contains an excluded hostname, and cannot tell that an
  exclusion pointer has gone stale. All three matter here: the router's hostname-derived values
  live in ConfigMaps whose whole `/data` is excluded, and one of those keys is rendered only for
  a campaign (SUP-211). It also checks that each pointer resolves to a real field, and that the
  chart's annotations and the listing's `evidence.exclude` agree in **both** directions — an
  exclusion the listing does not declare is a digest shown beside an incomplete answer, and one
  the listing declares and the chart no longer applies is a disclosure of something that is not
  happening. The second direction is invisible to the drift comparison: if the field stopped
  being rendered, nothing differs and nothing fails, while the listing goes on advertising that
  it was left out.

  What varies between the two shapes is the caller's to choose, and "two consumers" means
  everything a consumer brings rather than the hostname they typed: the router is also rendered
  with two different deployer addresses, which is the difference that no listing may ever declare
  because declaring it admits one deployer. Secret values are not compared — the platform's
  canonical rules drop `/data`, `/stringData` and `/immutable` from every Secret — which is both
  what makes a Secret the right carrier for such a value and what makes the comparison possible
  to ask about one at all.
- **Every placeholder has something that fills it** (`config_placeholders.py`). `router.yaml` is
  attested, so neither a secret nor a hostname is written into it — both are `${VAR}` the router's
  config loader substitutes from the environment. A placeholder with no value does not degrade:
  the loader throws before the first listener, so the deployment is a crash loop and no render
  error and no golden diff would have mentioned it. Checked in both directions, because a variable
  nothing refers to is dead configuration and, in the public ConfigMap's case, a field excluded
  from the snapshot for no reason.
- **The listing declares every image its charts render** (`declared_images.py`). A component's
  `images:` block is an allow-list and the render path is fail-closed on it, so an image the
  listing does not declare refuses the whole deployment — and a digest bumped in one file and not
  the other makes the listing advertise a pin the cluster never pulls. `helm template` reads
  neither the listing nor the digest it declares, so no golden diff can see either. A second
  container added to a chart is exactly the shape of change that walks into it.

- **The two containers of the egress agree about the two things between them.** The sidecar
  watches a file router-api renders and answers on a loopback port router-api polls, and nothing
  in a cluster checks that the two were told the same path, the same mount, the same port or a
  group that can actually open a 0640 file. Each disagreement is an egress that serves nothing
  with both containers Ready.

- **Misconfigurations are refused at render time.** Each one is a mistake that would deploy
  cleanly and then not work: an unpinned image, an Ingress with no class, a Garage key in a
  shape Garage refuses, a master key that is not 32 bytes, an AES key that is not 32 bytes.

## Smoke

`smoke/` installs a listing into a throwaway kind cluster and drives it through the Ingress
objects the charts render. Not in CI — it wants a cluster, an ingress controller and a build
of another repository's images — but it is what proves a chart deploys rather than renders:

```bash
CONFIDENTIAL_ROUTER=~/src/confidential-router charts/tests/smoke/run.sh
CONFIDENTIAL_S3=~/src/confidential-s3      charts/tests/smoke/confidential-s3.sh
```
