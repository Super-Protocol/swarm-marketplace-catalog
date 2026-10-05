# patroni-postgresql

A PostgreSQL cluster that survives losing a node, installed entirely inside one
namespace: three instances of the [Zalando Spilo](https://github.com/zalando/spilo)
image under [Patroni](https://github.com/patroni/patroni), electing a leader through
an etcd that is part of this chart, behind a TCP router that forwards to whichever
instance is currently the leader.

No operator. No CRDs. No cluster-scoped object of any kind, and — since 0.2.0 — **no
Kubernetes API call at all**: two `StatefulSet`s, a `Deployment`, four `Service`s,
three `ConfigMap`s, a `Secret` and three `PodDisruptionBudget`s, none of which needs
a service account token, and none of which is given one. That constraint is the reason
this chart exists rather than CloudNativePG: on the platform it is written for,
installing a CRD is a change to the cloud rather than to the application, and the path
that applies a deployment refuses any kind it has not been told about.

0.1.0 kept the leader lock in the Kubernetes API, which is Patroni's own recommended
arrangement on Kubernetes and does not work here. A cluster space confines its tenant
pods, and pod → kube-apiserver is one of the things the confinement blocks: the
connection to the API server's ClusterIP simply times out, Patroni logs
`K8sConnectionFailed('No more API server nodes in the cluster')`, no instance ever
passes its readiness probe, and `OrderedReady` holds the StatefulSet at 0/3 while
everything downstream waits forever. The alternative — asking the platform to
allowlist the API server for tenant pods — was considered and rejected: a chart does
not get to weaken a fail-closed confinement that every other tenant depends on
(SUP-215).

## Why, in one paragraph

A tenant volume on a Swarm cloud is provisioned onto its node's per-boot-ephemeral
state disk and pinned there. A node that reboots — including for a routine TCB update
— comes back with that volume empty, and a single-instance PostgreSQL on it comes
back *healthy and empty*: every probe passes, every connection succeeds, and the data
is gone (SUP-179). Replication across nodes is what converts that from total silent
loss into a switchover. It is the same bargain CockroachDB already makes on this
platform: the local disk is a cache of replicated state, and the replication is the
durability.

## What it gives you

```
<fullname>            the master Service — connect here, it follows the leader
<fullname>-repl       the standbys that are streaming, for a reader that tolerates lag
<fullname>-pods       the StatefulSet's governing Service, for stable pod DNS
<fullname>-etcd       the DCS's members, by their own stable names (headless)
```

```
<fullname>            3 PostgreSQL instances, one per node
<fullname>-etcd       3 etcd members holding the leader lock, one per node
<fullname>-router     2 HAProxy instances, each forwarding to the current leader
```

- **Automatic promotion.** The leader renews a lock with a TTL; a leader that stops
  renewing is replaced by a standby within roughly `patroni.ttl` seconds.
- **Automatic re-clone.** An instance that comes back with an empty data directory
  rebuilds itself from the current leader with `pg_basebackup`. Nobody is paged, and
  nothing has to notice that the volume was wiped.
- **RPO 0 for a single-node event.** `patroni.synchronousMode` means a commit is
  acknowledged only once a standby has it on disk, and Patroni will only promote a
  standby that was in sync.
- **One instance per node, required.** Two instances on one node share one ephemeral
  disk, which is the thing being defended against. On a cluster with fewer
  schedulable nodes than instances the extra ones stay Pending — visibly, rather
  than quietly next to a copy of themselves. The DCS and the router are placed the
  same way, for the same reason: a store or a proxy that went down with the node
  would make the database's own survival moot.
- **A throwaway DCS.** The leader lock, the member list and the live Patroni
  configuration are all etcd holds, and all three are re-derivable from the instances
  that have the database — so every member's volume is an `emptyDir`. One member
  replaced is re-admitted and caught up from the leader; all three replaced bootstrap
  a fresh cluster and the PostgreSQL members re-register into it. With
  `patroni.failsafeMode` on, neither event is a failover: a leader that cannot reach
  the DCS keeps serving as long as it can still reach every member it knows of.

## Install

```bash
helm install db charts/patroni-postgresql \
  --set auth.superuser.password=... \
  --set auth.replication.password=... \
  --set auth.username=myapp --set auth.database=myapp --set auth.password=...
```

As a subchart — the intended shape — the application points the database at the
Secret it already writes, and one password lives in one place:

```yaml
dependencies:
  - name: patroni-postgresql
    version: 0.2.0
    repository: "file://../patroni-postgresql"
    alias: postgresql
    condition: postgresql.enabled
```

```yaml
postgresql:
  fullnameOverride: myapp-postgresql     # also the Patroni scope, also the DSN's host
  auth:
    username: myapp
    database: myapp
    existingSecret: myapp                # the Secret the parent chart renders
    secretKeys:
      superuserPasswordKey: postgres-password
      replicationPasswordKey: replication-password
      userPasswordKey: password
```

Every value is documented where it is defined, in `values.yaml`. What follows is only
the handful of things that are not obvious from a value's name.

## The things that bite

**The master Service resolves to the router, not to an instance.** Which instance is
the primary is a question only Patroni's REST API answers, and a Service cannot ask
it — so HAProxy does, with `option httpchk GET /primary`, and the Service points at
HAProxy. Pointed at the database's own pods the name would resolve to all three
instances, two of which refuse writes, and the symptom would be a third of the queries
failing while the rest work.

**A failover is a few seconds longer than Patroni's own.** Promotion is bounded by
`patroni.ttl`; the name becoming the new primary then waits for the router's health
check (`router.check.interval × rise`), its readiness probe and the Service's
endpoints. Until all of it lands the master Service has no endpoints and a connection
to it is *refused* — which is the point of failing the router's readiness while there
is no primary: a client that retries, rather than one handed a connection to a standby
that rejects its writes. The smoke test measures it; it is a few seconds.

**The master Service's name is the Patroni scope.** Not because anything forces it any
more — it did while the lock was an `Endpoints` object — but so that `patronictl list`
and an application's DSN can be read together. `fullnameOverride` moves both.

**TLS is required by default, and the certificate is self-signed.** Spilo's
`pg_hba.conf` ends in `hostnossl all all all reject`, and it generates a certificate
per pod at start. A client that verifies certificates has to be told not to — libpq
`sslmode=require`, node-postgres `sslmode=no-verify` — and a client that cannot
negotiate TLS at all needs `requireSsl: false`, which adds `host all all all md5` and
drops the reject line. A deployment that gets this wrong looks like an application
that cannot reach a database which is perfectly healthy.

**No name in this chart carries its namespace.** Every pod-to-pod address is
`<pod>.<service>.$(POD_NAMESPACE).svc.<clusterDomain>`, with the namespace supplied at
run time — by the kubelet in a container's environment and arguments, and by HAProxy's
own variable expansion in `haproxy.cfg`. A namespace rendered into a manifest would
make the deployment's evidence digest a property of who deployed it, and the version
could then never declare an `expectedDigest` (marketplace spec §2.7).
`charts/tests/database_drift.py` is what keeps that true.

**An etcd member that comes back empty is re-admitted, not restarted.** The member id
etcd derives from a name and a peer URL is stable, so a replaced pod computes the id
the survivors already have — and a member they believe they have been talking to is
one they send a heartbeat to rather than a snapshot, which a member with an empty raft
log answers by panicking (`tocommit(N) is out of range [lastIndex(0)]`). So the init
container hands in the old identity and takes a new one — `member remove`, then
`member add` — over etcd's HTTP gateway, before etcd starts. The whole decision,
including why a cold start has to be told the opposite thing, is written out in
`templates/etcd-configmap.yaml`.

**A DCS that has lost its quorum and cannot get it back is replaced, not repaired.**
Two of three members replaced at once, while the third keeps its volume, leaves the two
crash-looping: they find nobody serving, try to bootstrap, and are told the cluster
already exists. Nothing in it is worth keeping, so the recovery is one command —
`kubectl delete pod -l app.kubernetes.io/name=<name>-etcd` — and failsafe mode is what
keeps the database serving while somebody runs it.

**`patroni.*` is bootstrap configuration.** `synchronousMode`, `ttl` and the rest are
written to the store once, when the cluster is first initialised. Changing them in
values and upgrading the chart changes the StatefulSet's environment and nothing about
the running cluster — the live values are edited with
`patronictl edit-config`.

**The application role's password is set once, at bootstrap.** The post-init hook
runs on the first leader only. Rotating the password afterwards is
`ALTER ROLE`, by hand, against the running cluster — the same limitation the Bitnami
chart this replaces had.

**A chart upgrade is not an in-place migration from another PostgreSQL chart.** A
`StatefulSet`'s selector is immutable, so replacing a differently-labelled
StatefulSet of the same name fails. Deploy alongside and move the data.

**Spilo will label pods through the API server unless it is told not to, and it
cannot be told through the environment.** Running under Kubernetes with a DCS that is
not the Kubernetes API, `configure_spilo.py` assigns `CALLBACK_SCRIPT =
callback_role.py` unconditionally, overwriting whatever was passed in — the Postgres
operator's arrangement, where a pod's role label is what a selector Service follows.
Here it would PATCH the pod through an API server the confinement blocks, retry ten
times over roughly eight minutes, and delay the post-promotion hook it is chained in
front of by exactly that long, on every promotion. The chart overrides the callbacks
through `SPILO_CONFIGURATION` instead; `patroni-postgresql.spiloConfiguration` in
`_helpers.tpl` says which one does what.

## What it deliberately does not do

- **No backups.** Spilo can ship WAL to S3 and this chart does not configure it: the
  design it was written for treats replication as the durability mechanism and an
  export as separate, optional insurance. Nothing here stops a listing adding the
  `WAL*` environment, and it would be a value, not a redesign.
- **No connection pooler.** The router is a TCP proxy and nothing more — no session
  pooling, no transaction pooling, no prepared-statement rewriting. A pooler would be
  a different component with its own failure modes, and nothing here needs one.
- **No read/write split for you.** The replica Service exists; which queries go to it
  is the application's decision, not the chart's.
- **No host-failure tolerance.** Three instances on three cVMs on one physical
  machine survive a node. They do not survive the machine.
- **No automatic major-version upgrade.** The PostgreSQL major version is part of the
  image name (`spilo-17`), on purpose: a major upgrade is a deliberate change to a
  line in `values.yaml` and a procedure, not something a tag does quietly.

## Tests

```bash
charts/tests/run.sh                  # lint, golden renders, and the refusals
charts/tests/smoke/patroni.sh        # a three-node kind cluster, and a real failover
```

The smoke is the one that matters, and it runs **with pod → kube-apiserver blocked**,
which is the condition that broke 0.1.0 and the only way to know that 0.2.0 does not
depend on it. It installs the chart, proves from inside a database pod that the API
server is unreachable and the DCS is, waits for three instances on three nodes, writes
a row, destroys the leader's pod, and checks that a standby was promoted, that the
master Service followed it, and that the row is still there. Then it deletes an
instance's volume as well as its pod — the wiped-node shape — and checks that the
instance rebuilt itself and has the data. Then it does the same two things to the DCS:
one member thrown away, and then all three at once, each time checking that the
database kept its leader and its data.
