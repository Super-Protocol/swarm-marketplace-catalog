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
| `api-bootstrap-token` | the SUP-95 auth seam: the one `CR_API_*` env var, rendered only when a token is set |
| `api-password-no-mailer` | what the listing deploys (SUP-112): passwords on with `mailer: none`, which has to render a config the router boots on |
| `api-password-off` | passwords off: the `auth.password` block is absent rather than `enabled: false`, so the chart stays bootable on an image that predates the key |
| `api-invite-only` | the SUP-173 auth seam: `requireInviteForSignUp` rendered only while it is on, so the chart stays bootable on an image that predates the key |
| `api-endpoint-hostname` | an endpoint that names a hostname of its own: a parameter, so it is rendered as a literal rather than as `${ROUTER_PUBLIC_HOSTNAME}`, and the public ConfigMap does not carry that key at all (SUP-211) |
| `ui-default` | the console's env and ingress; run.sh renders it against a second hostname as well, because one pinned image has to serve any API origin |
| `ollama-gpu-off` / `ollama-gpu-on` | the GPU switch |
| `s3-default` | what the confidential-s3 listing deploys: two hostnames, credentials derived from seeds, the bundled engine and the bundled PostgreSQL |
| `s3-virtual-host` | `server.s3_domains` in the gateway's config — the one line between an SDK that works out of the box and one that has to be told to use path style |
| `s3-external-postgres` | `postgresql.enabled: false` with a DSN of the deployment's own |
| `s3-explicit-credentials` | a real master key and a real Garage credential instead of seeds; the chart checks both shapes rather than trusting them |
| `s3-large-volumes` | the large end of the form, and the engine's layout capacity, which is derived from the data volume rather than set beside it |

The GPU cases render **otwld's `ollama` chart**, not one of ours: the listing
deploys that chart by `repoUrl` and the GPU switch lives in it. They are here
because a deployment of this listing with `gpuEnabled` on and no device on the
node schedules nothing at all, and the values that avoid it are worth pinning
somewhere. They need network the first time, to pull the chart.

## Beyond lint and goldens

Nine checks in `run.sh` are not a golden diff, and each exists because a golden diff
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
- **Every placeholder has something that fills it** (`config_placeholders.py`). `router.yaml` is
  attested, so neither a secret nor a hostname is written into it — both are `${VAR}` the router's
  config loader substitutes from the environment. A placeholder with no value does not degrade:
  the loader throws before the first listener, so the deployment is a crash loop and no render
  error and no golden diff would have mentioned it. Checked in both directions, because a variable
  nothing refers to is dead configuration and, in the public ConfigMap's case, a field excluded
  from the snapshot for no reason.
- **Misconfigurations are refused at render time.** Each one is a mistake that would deploy
  cleanly and then not work: an unpinned image, an Ingress with no class, a Garage key in a
  shape Garage refuses, a master key that is not 32 bytes.

## Smoke

`smoke/` installs a listing into a throwaway kind cluster and drives it through the Ingress
objects the charts render. Not in CI — it wants a cluster, an ingress controller and a build
of another repository's images — but it is what proves a chart deploys rather than renders:

```bash
CONFIDENTIAL_ROUTER=~/src/confidential-router charts/tests/smoke/run.sh
CONFIDENTIAL_S3=~/src/confidential-s3      charts/tests/smoke/confidential-s3.sh
```
