# patroni-postgresql

A PostgreSQL cluster that survives losing a node, installed entirely inside one
namespace: three instances of the [Zalando Spilo](https://github.com/zalando/spilo)
image under [Patroni](https://github.com/patroni/patroni), with the Kubernetes API as
the store they elect a leader through.

No operator. No CRDs. No ClusterRole, no webhook, no cluster-scoped object of any
kind — a `StatefulSet`, four `Service`s, a `ServiceAccount` with a namespaced `Role`,
a `ConfigMap`, a `Secret` and a `PodDisruptionBudget`. That constraint is the reason
this chart exists rather than CloudNativePG: on the platform it is written for,
installing a CRD is a change to the cloud rather than to the application, and the
path that applies a deployment refuses any kind it has not been told about.

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
<fullname>-config     the other half of the leader election store (headless, no endpoints of its own)
<fullname>-pods       the StatefulSet's governing Service, for stable pod DNS
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
  than quietly next to a copy of themselves.

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
    version: 0.1.0
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

**The master Service has no selector, and must not get one.** Patroni's leader lock
*is* an `Endpoints` object named after the scope, and winning an election is the same
write as pointing that object at the new leader. Give the Service a selector and
Kubernetes' endpoints controller takes the object over: the name then resolves to all
three instances, two of which refuse writes, and the symptom is a third of the
queries failing while the rest work.

**The master Service's name is the Patroni scope.** They are the same object's name,
so they cannot be configured apart. `fullnameOverride` moves both.

**TLS is required by default, and the certificate is self-signed.** Spilo's
`pg_hba.conf` ends in `hostnossl all all all reject`, and it generates a certificate
per pod at start. A client that verifies certificates has to be told not to — libpq
`sslmode=require`, node-postgres `sslmode=no-verify` — and a client that cannot
negotiate TLS at all needs `requireSsl: false`, which adds `host all all all md5` and
drops the reject line. A deployment that gets this wrong looks like an application
that cannot reach a database which is perfectly healthy.

**The leader election store is Endpoints, not ConfigMaps**, and switching is not
offered. Both modes work; only one of them keeps a value that changes every ten
seconds out of the deployment-evidence snapshot, because the platform's canonical
rules drop `Endpoints` as operational noise and collect `ConfigMap`s.

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

**RBAC is exactly what Patroni 4.0 calls**, read off its source rather than copied
from the operator's ClusterRole: `endpoints` and `pods`, and nothing on `services` or
`secrets`. `templates/role.yaml` says which call needs which verb and why the two
obvious ones are absent.

## What it deliberately does not do

- **No backups.** Spilo can ship WAL to S3 and this chart does not configure it: the
  design it was written for treats replication as the durability mechanism and an
  export as separate, optional insurance. Nothing here stops a listing adding the
  `WAL*` environment, and it would be a value, not a redesign.
- **No connection pooler.** No PgBouncer, no Pgpool. A pooler in front of the master
  Service is another hop with its own failure modes, and the thing it would be
  solving — following the leader — is already solved by the Service.
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

The smoke is the one that matters. It installs the chart, waits for three instances on
three nodes, writes a row, destroys the leader's pod, and checks that a standby was
promoted, that the master Service followed it, and that the row is still there. Then
it deletes an instance's volume as well as its pod — the wiped-node shape — and checks
that the instance rebuilt itself and has the data.
