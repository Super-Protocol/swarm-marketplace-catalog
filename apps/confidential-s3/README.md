# Confidential S3

S3-compatible object storage that runs inside a confidential environment, with a console for
buckets, keys and access. A deployment publishes signed evidence of what it is running, and a
service account can be confined so that it only works for a caller whose own evidence matches what
you approved.

## What a deployment is

Five workloads in one cluster space, from one chart:

| | |
|---|---|
| **Data plane** | The only thing that speaks S3. Verifies SigV4, resolves the bucket, and either stores into the deployment's own engine or re-signs the request to a storage you registered. |
| **Control plane** | Owns the schema: workspaces, users, buckets, back storages, service accounts, grants and confinement policies. Reachable by the console and by nothing else. |
| **Console** | The browser console. A back-end-for-front-end — the browser talks to it, it talks to the control plane, so there is no second hostname for an API and no cross-origin sign-in to arrange. |
| **Object engine** | Garage, one node. Behind every `internal` bucket. Its S3 port is reachable by the data plane and by nothing else. |
| **PostgreSQL** | The control plane's database. Also what the data plane reads its three views from; it writes nothing. |

Two hostnames are published: the console, and the S3 endpoint. Nothing else in the deployment has
an address.

## Three kinds of bucket

- **Internal** — stored in the deployment's own engine, on the volume the form sizes. Nothing
  outside the cluster space reaches it.
- **External** — a storage you already have. The data plane re-signs each request with credentials
  the deployment holds sealed, so your own S3 account is never handed to the caller.
- **External-encrypted** — the same, with envelope encryption: object names are encrypted, contents
  are sealed in AES-256-GCM chunks under a per-bucket key wrapped by the deployment's master key,
  and range GET still works. What lands in the backing storage is unreadable without this
  deployment.

The bucket's mode is chosen when it is created, and it does not change afterwards.

## Confinement

A service account is an ordinary S3 credential by default. It can also be *confined*: told to work
only for a caller that presents attestation evidence matching a policy — a digest you paste, or
Rego if the shape of the rule is unusual. A confined credential that leaks is not a credential
anyone else can use.

## What it deliberately does not do

- **No sign-up.** There is no mailbox in this deployment, so there is no self-service registration
  and no password reset. The first administrator arrives with the first-sign-in token below and
  invites everybody else.
- **No virtual-host addressing, unless you arrange it.** `bucket.s3.example.com` needs a wildcard
  DNS record under the S3 hostname, and a marketplace writes one record for one name. The endpoint
  is path style out of the box, which an SDK has to be told: `forcePathStyle: true`, or
  `AWS_S3_ADDRESSING_STYLE=path`.
- **No replication.** The object engine is one node. Durability is the volume's, which is what a
  cluster space provides; this is not a design for surviving the loss of one.
- **No key recovery.** The master key is generated at deploy time and never shown. It wraps every
  encrypted bucket's key and seals every registered storage's credentials, so a deployment that
  loses it cannot read its own external buckets — and nothing re-wraps them. A service account's
  secret key is shown once, when it is issued; only its salt is stored.
- **No object storage in the database.** Contents never go to PostgreSQL. An encrypted bucket keeps
  one row per object there, with its wrapped key, so the database grows with how many objects there
  are rather than how large they are.

## Deploying

Fill in two hostnames and press deploy. The first-sign-in token appears with the outputs; open the
console, sign in with the marketplace account's address and that token, and the administrator
account is claimed. It is spent by the first account that uses it, whoever that is — so claim the
deployment before its hostname is handed out.

`resources.min` covers the default volumes. Choosing a larger object volume in the form means
asking for a larger cluster space at the same time.

## Evidence

`evidence.exclude` names the two Ingress hostnames, which are the only fields that differ between
two deployments of this version. `expectedDigest` is not declared yet: it cannot be derived from
the charts — a cluster defaults fields no template writes — so it takes two real deployments of the
images this version pins, and those images are not final. Adding it later does not change what is
rendered, so a digest measured on this version stays valid for the version that declares it.
