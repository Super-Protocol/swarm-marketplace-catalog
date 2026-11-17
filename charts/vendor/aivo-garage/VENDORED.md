# garage, vendored

| | |
|---|---|
| Upstream | `aivoagent/helm-charts`, directory `charts/garage/` |
| Commit | `8a6df6a513419b71ac8025c4e848a84624ae2686` (2026-11-06) |
| Server | Garage 2.3.0, S3-compatible, one node and one PVC |

Vendored unmodified, under `aivo-garage/` so the directory name says where it came from.

## Why this replaced MinIO

Two reasons, and the first one is fatal on its own.

**MinIO's images are no longer anonymously pullable.** `quay.io/minio/minio` and
`quay.io/minio/mc` answer `401 UNAUTHORIZED` to an unauthenticated manifest request for every
tag, including the two that listing 3.0.9 pinned (`RELEASE.2023-06-19T19-52-50Z` and
`RELEASE.2023-06-19T19-31-19Z`), and `docker.io/minio/minio` answers
`insufficient_scope: authorization failed` — checked 2026-11-17. A deployment of that listing
has no object storage, which is where the agent keeps every uploaded file and every export, so
the digest could not be declared for an image that cannot even be fetched.

**And it is what the vendor ships for 1.x.** Garage is the S3 layer of aivo's own universal
delivery profile; MinIO is not in it any more. Following that keeps this listing on the path the
product is actually tested against rather than on one of our own.

The chart creates the access key, the RPC secret, the layout and the buckets itself, from a
Secret, with a post-install Job that drives the admin API — the server is built `FROM scratch`
and has no shell for a CLI to be run in. The key requirements are the server's own and the
chart refuses to render a pair it would reject at boot: an access key id of at least 8
characters, a secret of at least 16, both printable ASCII.
