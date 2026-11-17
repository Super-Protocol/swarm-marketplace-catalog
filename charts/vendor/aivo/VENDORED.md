# aivo, vendored

| | |
|---|---|
| Upstream | `aivoagent/helm-charts`, directory `aivo/` |
| Commit | `8a6df6a513419b71ac8025c4e848a84624ae2686` (2026-11-06) |
| Serves | aivo agent **1.17.0** (released 2026-11-10) |

`8a6df6a` is the last upstream commit before 1.17.0 was tagged. The pin is deliberate:
chart and platform move together upstream — the distribution builder gates the pair in both
directions — so the chart that belongs to 1.17.0 is the one that existed when 1.17.0 shipped,
not the current tip, which has since carried 1.18 work (`mcp-weather`, among others).

## What was dropped

`docs/` and `prefect-kb-deploy/` — neither is a dependency of the `aivo` chart and neither has
anything to do with this listing. A documentation site and a Prefect job in the deployment's
namespace would be two more images to pin and two more things in the quota.

## Local deviations

Each one is marked `LOCAL DEVIATION` in the file it touches, so a re-vendoring notices it.

### `appVersion: 1.16.0` → `1.17.0`

Not cosmetic: `retail-ai` uses `.Chart.AppVersion` as the default image tag, so this is the
version the chart serves when the listing does not say otherwise. The listing does say
otherwise — every image is pinned there and declared by digest — but a chart whose declared
app version is not the one it runs is a chart that will mislead somebody.

### `charts/config`: the configuration document is split

`configOverlay` (values), `templates/config-overlay.yaml` (new) and three changes in
`templates/deployment.yaml`.

Upstream renders the whole platform configuration into a ConfigMap. That document carries
every credential the services have — the database passwords, the Redis password, the
object-store secret key, the Keycloak client secret, the model access key — which is correct
for an installation an engineer applies by hand and wrong for a marketplace deployment: the
manifests are digested into signed evidence that is served publicly at
`/.well-known/swarm-evidence`, so a credential in a ConfigMap is a credential published.

Excluding `/data/services.yaml` from the evidence — what listing 3.0.9 did — buys the secrecy
by hiding the entire configuration from attestation. That is the wrong trade for this product:
a deployment whose claim is "the model is inside your perimeter" has to let a verifier read
*where the model is* out of the signed snapshot.

The same document also carries the public hostname, in the UI's GraphQL endpoints and its
logout URL, so its digest would be the consumer's rather than the version's whatever was done
about the credentials.

So the ConfigMap keeps every endpoint, model id, threshold and egress allowlist, attested in
full, with each withheld value written as a `${NAME}` reference; a Secret carries what those
names stand for, and the platform lifts every rendered Secret out of the bundle and seals it
for the target. An init container substitutes one into the other before the service reads
`conf/`, in the service's own image, with the substitution rule spelled out in the Deployment
— which is itself attested — rather than mounted from somewhere a verifier would have to go
and find.

Only two kinds of value may go in the overlay: a credential, and a value that differs in
every deployment. Everything else stays attested.

It fails closed twice: on a `${NAME}` the Secret does not carry, and on a reference that is
not the whole value of its key (a credential is substituted as a JSON-encoded scalar, which
is what makes a password containing a quote survive; pasting one into the middle of a string
would corrupt the document instead of filling it in).

**If this is ever upstreamed, delete the deviation rather than merging it.**
