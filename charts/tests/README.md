# Chart tests

`helm lint` over the three confidential-router charts, and a rendered-output golden for
each case in [`cases.tsv`](./cases.tsv). Run them from the repository root:

```bash
charts/tests/run.sh            # lint + render + diff
UPDATE=1 charts/tests/run.sh   # rewrite the goldens after an intended change
```

A golden holds only the documents the case's own chart produced —
`# Source: <chart>/templates/…`. Subchart output is dropped: a vendored
PostgreSQL bump would otherwise rewrite every golden that installs one, and
those hundreds of lines are the vendor's contract, not this repository's.

The release name and namespace are fixed (`cr`, `confidential-router`) so the
output does not move with whoever ran it.

## The cases

| Case | Axis it pins |
| --- | --- |
| `api-one-model` / `api-three-models` | the model booleans: `models[]` and `endpoints[]` in the rendered `router.yaml` are built from them |
| `api-billing-manual` / `api-billing-stripe` | the billing mode, and the `NODE_ENV` that follows from it — the manual provider is refused in production |
| `api-no-models` | every model off: an empty catalogue renders, rather than a chart that cannot be installed |
| `api-external-postgres` | `postgresql.enabled: false` with a DSN of the deployment's own |
| `litellm-one-model` / `litellm-three-models` | the same booleans on the other side, so the two charts' model names can be diffed against each other |
| `ui-default` | the console's env and ingress, including the API-origin check |
| `ollama-gpu-off` / `ollama-gpu-on` | the GPU switch |

The GPU cases render **otwld's `ollama` chart**, not one of ours: the listing
deploys that chart by `repoUrl` and the GPU switch lives in it. They are here
because a deployment of this listing with `gpuEnabled` on and no device on the
node schedules nothing at all, and the values that avoid it are worth pinning
somewhere. They need network the first time, to pull the chart.
