# confidential-s3

The whole of Confidential S3 in one chart: the S3 data plane, the control plane, the console, the
object engine behind internal buckets, and the PostgreSQL the control plane owns.

```bash
helm install confidential-s3 swarm-marketplace/confidential-s3 \
  --set consoleHostname=console.example.com \
  --set s3Hostname=s3.example.com \
  --set masterKey.seed="$(openssl rand -hex 32)" \
  --set engine.seed="$(openssl rand -hex 32)" \
  --set bootstrapToken.value="$(openssl rand -hex 32)" \
  --set database.password="$(openssl rand -hex 24)" \
  --set api.bootstrap.adminEmail=you@example.com \
  --set gateway.image.digest=sha256:… \
  --set api.image.digest=sha256:… \
  --set console.image.digest=sha256:… \
  --set garage.image.digest=sha256:…
```

## Why one chart and not four

The five workloads share a database, a master key and a private network, and two of them are only
correct if they were rendered from the same values: the credential the bootstrap Job imports into
the engine has to be the one the data plane holds. Split across components, that agreement becomes
something a person arranges. Here it is something the chart cannot get wrong.

## Why Garage is not a subchart

The upstream chart (`script/helm/garage` in the Garage repository) creates a `ClusterRole` and a
`ClusterRoleBinding` unconditionally, for the Kubernetes discovery mechanism a single node does not
need — cluster-scoped objects a marketplace cluster space cannot create. Its Service also publishes
only the S3 and web ports, and the bootstrap Job has to reach the admin API. What is left of it
after removing both is smaller than `templates/garage.yaml`, and vendoring an upstream chart in
order to rewrite half of it is a fork wearing somebody else's version number.

PostgreSQL *is* a vendored subchart, because nothing about it had to change.

## Seeds, not secrets

Three credentials in this chart are derived rather than supplied:

| Value | Shape it must have | Derived as |
|---|---|---|
| Master key | 32 bytes | `sha256(masterKey.seed)` |
| Engine access key | `GK` + 24 hex | `"GK" + sha256("…engine-access-key\|" + engine.seed)[:24]` |
| Engine secret key | 64 hex | `sha256("…engine-secret-key\|" + engine.seed)` |

Garage refuses a key in any other shape with a 400, and the control plane refuses a master key of
any other length at startup. A marketplace `generated` parameter is a random string with no shape at
all — so the shapes are derived here, from seeds, with one domain separator each.

That is also why the derivation is in the chart and not in the Job. A published cluster space is
frozen: a Job that asked Garage for a credential would have nowhere to write what it got back, and
the data plane reads its copy from a Secret that was sealed before the Job ever ran. Deriving both
sides from one seed is what lets them agree without anything being written down.

`masterKey.value`, `engine.accessKey` and `engine.secretKey` are the escape hatches for a deployment
that already has real ones; the chart checks their shape rather than trusting them.

## What is published

Two Ingresses: the console, and the S3 endpoint. The control plane has a Service and no Ingress —
the console is its only caller — and the engine has neither an Ingress nor a route to one.

Both Ingresses carry `swarm.io/exclude-evidence-fields: /spec/rules/0/host`. The hostname is the
only field that differs between two deployments of this version, and a digest that varied with it
would be one nobody else could match. The listing declares the same exclusion in `evidence.exclude`,
which is what the marketplace reads to show it beside the digest.

## The bootstrap Job

A started Garage accepts no writes: it has no storage layout, and the gateway's credential does not
exist in it. Both are one-time cluster decisions rather than configuration, so neither can live in
`garage.toml`. `files/garage-bootstrap.mjs` does them over the admin API, and every step is safe to
repeat — the layout is skipped once any role exists, and an already-imported key answers 409, which
the script reads as success. That matters because the platform deletes and re-creates Jobs on every
reconfigure.

It runs the control plane's image, which already carries Node. A fourth image would be a fourth
digest for the listing to declare and keep current, for a container that runs for four seconds.

The file is a copy of `deploy/garage-bootstrap.mjs` in
[Super-Protocol/confidential-s3](https://github.com/Super-Protocol/confidential-s3), where it is
developed and where the repository's own image smoke test runs it.

## Addressing

`virtualHostStyle` is off. Virtual-host addressing (`bucket.s3.example.com`) is what AWS SDKs use
unless told otherwise, and it needs a wildcard DNS record under the S3 hostname — which a
marketplace, writing one record for one name, does not provide. With it off the endpoint is path
style and a client has to say so (`forcePathStyle`, `AWS_S3_ADDRESSING_STYLE=path`). Turn it on only
where the wildcard exists: an unlisted domain must never have its first host label read as a bucket
name, so this is a decision rather than something the gateway could guess.

## Values worth knowing

| Value | Default | Notes |
|---|---|---|
| `consoleHostname`, `s3Hostname` | — | Required, and they must differ. |
| `publicScheme` | `https` | Only `http` for a local cluster. |
| `garage.persistence.data.size` | `20Gi` | Also what the engine advertises as its layout capacity; the Job derives one from the other. |
| `postgresql.enabled` | `true` | `false` needs `database.url` or `database.host` + `database.password`. |
| `gateway.cacheTtlSeconds` | `5` | How long a console change takes to reach the data plane. There is no invalidation channel. |
| `gateway.chunkSize` | `4194304` | Plaintext bytes per AES-256-GCM chunk, for external-encrypted buckets. Recorded per object, so changing it never makes what is stored unreadable. |

## Tests

`charts/tests/run.sh` renders this chart against the cases in `charts/tests/cases/` and diffs them
against the goldens, and checks that each of the misconfigurations that would deploy cleanly and
then not work is refused at render time.

`charts/tests/smoke/confidential-s3.sh` installs it into a kind cluster with an ingress controller
and drives the result: first sign-in, a bucket, a service account, and an object put and read back
through the published S3 endpoint.
