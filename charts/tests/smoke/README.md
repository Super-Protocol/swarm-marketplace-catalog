# Smoke

Three scripts. Two are per listing — `run.sh` for confidential-router, `confidential-s3.sh`
for confidential-s3 — and build the images from a checkout of the application's repository,
install the chart into a throwaway kind cluster with an ingress controller in front of it,
and then ask the deployment for the things it exists to do, through the Ingress objects the
chart renders rather than through a port-forward that would prove the pods work and leave
the routing untried.

The third, `patroni.sh`, is per chart rather than per listing, because what it tests is not
a request path: it is whether `patroni-postgresql` elects a leader, promotes a standby when
the leader's node is taken away, and rebuilds an instance whose volume was wiped. None of
that is visible in a render, and all of it is the reason the chart exists.

It also runs with **pod → kube-apiserver blocked**, which is what a cluster space does to
its tenant pods and what the 0.1.0 chart could not bootstrap behind (SUP-215). Behind that
block the Kubernetes-DCS version sits at 0/3 forever; this run is the evidence that the
etcd version does not.

## patroni-postgresql

```bash
charts/tests/smoke/patroni.sh
KEEP=1 charts/tests/smoke/patroni.sh
```

Three *schedulable* nodes, because the chart's one-per-node anti-affinity is `required` and
a smaller cluster would leave two instances Pending and prove nothing. Every image is
public, so this pulls rather than builds — onto every node up front, since otherwise
`podManagementPolicy: OrderedReady` turns Spilo into three sequential 600 MB pulls.

How the confinement is simulated: CoreDNS and the local-path provisioner are moved to the
control-plane node, which stays unconfined — in a real cluster space they are on the
platform's side of the boundary for the same reason — and then every worker gets a `DROP`
in its `FORWARD` chain for the API server's address. Pod traffic crosses `FORWARD`;
host-network traffic does not, so the kubelet, kube-proxy, kindnet and `kubectl exec` are
all untouched, and a pod gets the TCP timeout a confined space gives it rather than a
refusal.

- the API server is unreachable from inside a database pod, the DCS on the same namespace's
  network is reachable, and no pod in the deployment has a service account token to make an
  API call with even if it could;
- the DCS's three members come up on three distinct nodes, each having bootstrapped the
  cluster rather than tried to join one;
- three instances become ready on three distinct nodes, which means each cloned itself from
  the leader before reporting in;
- `patronictl list` shows one leader, one synchronous standby and one asynchronous replica —
  the shape `synchronous_node_count: 1` of two standbys is supposed to produce;
- the application role and database the post-init hook created exist, and the application
  connects **through the master Service** and lands on the primary;
- the master Service resolves to the two router instances, which is what makes "connect to
  the database" mean "connect to the leader" now that the leader lock is in etcd;
- a committed row survives the leader's node being cordoned and its pod destroyed: a standby
  is promoted, the master Service follows it, and the row is still there. The cordon is
  load-bearing — see the comment in the script. The run prints how long the name took to
  mean the new primary, which is Patroni's promotion plus the router's health check,
  readiness and endpoints;
- the destroyed instance re-joins and streams again;
- and then the SUP-179 shape itself: an instance whose **volume** is deleted along with its
  pod re-clones from the current leader and comes back with the data. That is the case a
  single-instance database came back from healthy and empty;
- one DCS member is thrown away — the rolling-update and drained-node case — and is
  re-admitted to the cluster it was already a member of with an empty volume;
- and then **every** DCS member at once, which is the cloud-wide reboot: the leader lock,
  the member list and the cluster configuration all go, a fresh cluster forms from the same
  static member list, the PostgreSQL members re-register into it, and the leader is still
  the same leader — because failsafe mode held the lock while the store was gone. A write
  lands afterwards, which is the only way to know that all of it ended somewhere usable.

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
