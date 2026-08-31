# Smoke

`run.sh` installs the three charts into a throwaway kind cluster — with the
`ollama` chart the listing deploys alongside them and the PostgreSQL the API
chart brings — and then asks the deployment for the things it exists to do:

- every workload becomes ready, including the migration init container against a
  PostgreSQL that is not up yet when the pod is first scheduled;
- `/health` reports the database round trip, and is **not** reachable from
  outside the cluster;
- the console serves `/login` through its own Ingress;
- a magic-link sign-in, a manual top-up and an API key, obtained the way the
  console obtains them — there is no path that reaches into the database;
- `GET /v1/models` lists exactly the models the chart's booleans switched on, and
  LiteLLM publishes the same names;
- a generation is answered through LiteLLM and Ollama and is metered;
- a streamed generation arrives as more than one chunk, as `text/event-stream`,
  through the Ingress — which is what the SSE annotations are for.

```bash
CONFIDENTIAL_ROUTER=~/src/confidential-router charts/tests/smoke/run.sh
KEEP=1 CONFIDENTIAL_ROUTER=~/src/confidential-router charts/tests/smoke/run.sh   # leave it up
```

## Why it builds the images

`ghcr.io/super-protocol/confidential-router/{router-api,router-ui}` are private
packages of the org. A cluster that deploys this listing needs a pull secret for
them; this script has none, so it builds both from a checkout and loads them into
the node. `ROUTER_API_IMAGE` / `ROUTER_UI_IMAGE` skip the build if you already
have them.

The console image has to be built with `NEXT_PUBLIC_API_ORIGIN=http://$API_HOST`
— `next build` inlines it, and the chart refuses to render a console built for a
different origin than the one it is being deployed against.

## Why it is not in CI

It wants a kind cluster, an ingress controller, ~3 GB of model weights and a
build of another repository's images. The golden tests in
[`../run.sh`](../run.sh) are what CI runs; this is what a human runs before
changing something structural, and what produced the two fixes recorded in the
SUP-93 pull request.
