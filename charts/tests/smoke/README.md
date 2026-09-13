# Smoke

Two scripts, one per listing: `run.sh` for confidential-router, `confidential-s3.sh` for
confidential-s3. Both build the images from a checkout of the application's repository,
install the chart into a throwaway kind cluster with an ingress controller in front of it,
and then ask the deployment for the things it exists to do — through the Ingress objects the
chart renders, not through a port-forward that would prove the pods work and leave the
routing untried.

## confidential-s3

```bash
CONFIDENTIAL_S3=~/src/confidential-s3 charts/tests/smoke/confidential-s3.sh
KEEP=1 CONFIDENTIAL_S3=~/src/confidential-s3 charts/tests/smoke/confidential-s3.sh
```

- every workload becomes ready, and the bootstrap Job completes — a started Garage accepts no
  writes until it has one;
- only the console and the S3 endpoint have a hostname: the control plane has a Service and
  no Ingress, and the engine has neither;
- the console serves `/login` through its own Ingress;
- the first-sign-in token redeems for the administrator the parameters seeded, and a bucket
  and a service account are created the way the console creates them;
- an object is put and read back **byte for byte** through the published S3 endpoint with a
  signed request, and an unsigned one is refused;
- the bootstrap Job completes **a second time** against an engine that is already
  bootstrapped. The platform deletes and re-creates Jobs on every reconfigure, so that is not
  a hypothetical: a Job that failed the second time would take every reconfigure with it.

## confidential-router

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
- `GET /v1/models` lists exactly the models the chart's `models` list selected,
  and LiteLLM publishes the same names;
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

The console image takes no build arguments: it reads its API origin from the
environment the chart sets, so the same image serves whatever `API_HOST` this run
happens to use.

## Why it is not in CI

It wants a kind cluster, an ingress controller, ~3 GB of model weights and a
build of another repository's images. The golden tests in
[`../run.sh`](../run.sh) are what CI runs; this is what a human runs before
changing something structural, and what produced the two fixes recorded in the
SUP-93 pull request.
