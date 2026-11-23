# aivo, vendored

| | |
|---|---|
| Upstream | `aivoagent/helm-charts`, directory `aivo/` |
| Commit | `8a6df6a513419b71ac8025c4e848a84624ae2686` (2026-11-06) |
| Serves | aivo agent **1.22.1** (released 2026-11-22) |
| Re-checked | 2026-11-23 against upstream `915eb81` — the tip, and the last commit before 1.22.1 (SUP-896) |

`8a6df6a` was the last upstream commit before 1.17.0 was tagged, and it is still the pin after
the move to 1.22.1. That is a decision, not an omission: upstream has made exactly two changes
to `aivo/` since, and neither belongs in this deployment.

  * `1a64403` adds the **`mcp-weather` subchart** — `aivo/charts/mcp-weather`, three lines of
    `Chart.yaml` dependency and `mcp-weather.enabled: false` in `values.yaml`. Off by default,
    so taking it would change no rendered manifest; not taken anyway, because a connector whose
    whole function is calling `api.open-meteo.com` is one more image to pin, one more thing in
    the quota, and an outbound channel in a listing whose claim is that it has none. The
    existing `mcp-telegram` and `mcp-email` subcharts are vendored and off for the same reason;
    this one is not vendored at all, which is the stronger statement.
  * `915eb81` is in `charts/keycloak`, a chart this listing does not use — it runs the vendored
    bitnami Keycloak. The CVE it mitigates applies to that one too; the mitigation is therefore
    carried in `conversational-analyst-identity` instead. See that chart's `Chart.yaml`.

The pin stays an upstream commit the chart's own platform version shipped against, so chart and
platform still move together the way the distribution builder expects.

## What was dropped

`docs/` and `prefect-kb-deploy/` — neither is a dependency of the `aivo` chart and neither has
anything to do with this listing. A documentation site and a Prefect job in the deployment's
namespace would be two more images to pin and two more things in the quota.

## Local deviations

Each one is marked `LOCAL DEVIATION` in the file it touches, so a re-vendoring notices it.

### `appVersion: 1.16.0` → `1.22.1`

Not cosmetic: `retail-ai` uses `.Chart.AppVersion` as the default image tag, so this is the
version the chart serves when the listing does not say otherwise. The listing does say
otherwise — every image is pinned there and declared by digest — but a chart whose declared
app version is not the one it runs is a chart that will mislead somebody. It read `1.17.0`
while this listing ran agent 1.17 and reads `1.22.1` now (SUP-896).

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

### `charts/config`: the init container reads the ConfigMap mount as a directory of keys

`templates/deployment.yaml`, in the same init container as the deviation above.

A ConfigMap volume is not a flat directory: the kubelet's atomic writer puts the data in a
timestamped directory, points `..data` at it and makes every key a symlink into that.
`fs.readdirSync` returns all three kinds of entry, and reading each one blindly read the
timestamped directory — `EISDIR: illegal operation on a directory` — so the init container died
on the first boot and on every retry, `agent-config` stayed in `Init:CrashLoopBackOff`, and the
five services that wait for the configuration service restarted behind it. Found on the
`azure-test` stand, 2026-11-21 (SUP-711); the upstream chart has the same code.

Entries whose name begins with `..` are skipped and each remaining one is confirmed to be a
file (`statSync` follows the key symlink), and an empty source directory is now an error rather
than a pod that reports success with no configuration.

**If this is ever upstreamed, delete the deviation rather than merging it.**
