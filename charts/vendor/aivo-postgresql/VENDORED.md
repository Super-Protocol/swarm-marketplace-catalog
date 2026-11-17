# postgresql (pgvector), vendored

| | |
|---|---|
| Upstream | `aivoagent/helm-charts`, directory `charts/postgresql/` |
| Commit | `8a6df6a513419b71ac8025c4e848a84624ae2686` (2026-11-06) |
| Image | `pgvector/pgvector`, the official postgres image with the `vector` extension built in |

Vendored unmodified, under `aivo-postgresql/` so it does not collide with the bitnami
`postgresql` chart `confidential-s3` depends on. Its own `Chart.yaml` still names it
`postgresql`, which is what the dependency entry has to match.

Why not the bitnami chart, which this repository already vendors: the agent needs `pgvector`
for its knowledge index, and bitnami's images do not carry the extension — checked upstream on
`16.6.0-debian-12-r0` and `17.3.0-debian-12-r1`, whose `share/extension` has no `vector`.
Without it the knowledge index comes up with no embedding column and schema search silently
answers from text alone: the agent starts, the deployment looks healthy, and the assistant
cannot reliably find the right table.

This chart also creates the databases, roles and extensions from its init **Secret** rather
than from a ConfigMap, which is what retired the SQL ConfigMap and bootstrap Job that
`conversational-analyst-platform` 0.3.x shipped.
