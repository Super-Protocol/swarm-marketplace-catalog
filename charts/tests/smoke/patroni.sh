#!/usr/bin/env bash
#
# What a golden render cannot tell you about a database: whether it elects a leader,
# whether a standby clones itself from that leader, whether the master Service follows
# a failover, and whether anything was lost on the way.
#
#   charts/tests/smoke/patroni.sh
#   KEEP=1 charts/tests/smoke/patroni.sh     # leave the cluster up afterwards
#
# Needs: kind, kubectl, helm, docker. The Spilo image is public, so unlike the router
# smoke this pulls rather than builds.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
cd "$root"

CLUSTER=${CLUSTER:-patroni-smoke}
NS=${NS:-patroni}
RELEASE=${RELEASE:-pg}
# The master Service's name, which is also the Patroni scope.
SCOPE=${SCOPE:-router-postgresql}
APP_USER=router
APP_DB=router
APP_PASSWORD=smoke-app-password
SUPERUSER_PASSWORD=smoke-superuser-password
REPLICATION_PASSWORD=smoke-replication-password

SPILO_IMAGE=$(
  python3 - <<'PY'
import yaml
v = yaml.safe_load(open("charts/patroni-postgresql/values.yaml"))["image"]
print(f"{v['registry']}/{v['repository']}@{v['digest']}")
PY
)

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

cleanup() {
  local code=$?
  if [ $code -ne 0 ]; then
    printf '\n--- pods ---\n' >&2
    kubectl get pods -n "$NS" -o wide >&2 || true
    kubectl logs -n "$NS" "$SCOPE-0" --tail=80 >&2 || true
  fi
  if [ -z "${KEEP:-}" ]; then
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  else
    printf '\nCluster %s left up. Delete it with: kind delete cluster --name %s\n' "$CLUSTER" "$CLUSTER"
  fi
}
trap cleanup EXIT

# `patronictl` inside a pod, which is the only place it is configured. Any instance
# answers: they all read the same DCS.
patronictl() { kubectl exec -n "$NS" "$1" -c postgresql -- patronictl list -f json 2>/dev/null; }

# What Patroni reports for each member, one `name role state` line each. A member
# that has never reported in has neither a role nor a state, which is something the
# assertions below are looking for rather than a reason to crash.
roles() { patronictl "$1" | python3 -c 'import json,sys
for m in json.load(sys.stdin): print(m["Member"], m.get("Role","-"), m.get("State","-"))'; }

leader_of() {
  patronictl "$1" | python3 -c 'import json,sys
print(next((m["Member"] for m in json.load(sys.stdin) if m.get("Role") == "Leader"), ""))'
}

psql_as_app() {
  kubectl exec -n "$NS" "$1" -c postgresql -- env "PGPASSWORD=$APP_PASSWORD" \
    psql -qtAX "host=$SCOPE port=5432 user=$APP_USER dbname=$APP_DB sslmode=disable" -c "$2"
}

step "Image"
docker image inspect "$SPILO_IMAGE" >/dev/null 2>&1 || docker pull "$SPILO_IMAGE"
ok "$SPILO_IMAGE"

step "Cluster"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --config "$here/patroni-kind.yaml" --wait 240s >/dev/null
# Pulled onto every node up front, in parallel. `kind load docker-image` cannot be
# used here: it imports with `--all-platforms`, and a multi-architecture image pulled
# by digest on one architecture has no blobs for the others.
#
# It is also the only part of the run that is serialised by `podManagementPolicy:
# OrderedReady` if it is left to the kubelet — three sequential pulls of a 600 MB
# image before the first instance has even run initdb.
for node in $(kind get nodes --name "$CLUSTER"); do
  docker exec "$node" crictl pull "$SPILO_IMAGE" >/dev/null &
done
wait
kubectl create namespace "$NS" >/dev/null
ok "3 nodes, Spilo present on each"

step "Install"
helm install "$RELEASE" charts/patroni-postgresql --namespace "$NS" \
  --set "fullnameOverride=$SCOPE" \
  --set "auth.superuser.password=$SUPERUSER_PASSWORD" \
  --set "auth.replication.password=$REPLICATION_PASSWORD" \
  --set "auth.username=$APP_USER" \
  --set "auth.database=$APP_DB" \
  --set "auth.password=$APP_PASSWORD" \
  --set requireSsl=false \
  --set persistence.size=1Gi \
  --set resources.requests.cpu=100m \
  --set resources.limits.cpu=500m \
  >/dev/null
ok "installed"

step "Every instance becomes ready"
# The first instance runs initdb and the post-init SQL, and each of the other two
# clones from the leader with pg_basebackup before it reports ready.
kubectl rollout status -n "$NS" "statefulset/$SCOPE" --timeout=900s >/dev/null
ok "3/3 ready"

step "One instance per node"
nodes=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql" \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort)
distinct=$(printf '%s\n' "$nodes" | sort -u | wc -l)
total=$(printf '%s\n' "$nodes" | wc -l)
if [ "$distinct" = "3" ] && [ "$total" = "3" ]; then
  ok "3 instances on 3 nodes"
else
  die "$total instances on $distinct node(s): $(printf '%s' "$nodes" | tr '\n' ' ')"
fi

step "One leader, two standbys, and the standbys are synchronous candidates"
roles "$SCOPE-0" | sed 's/^/        /'
leader=$(leader_of "$SCOPE-0")
[ -n "$leader" ] || die "no leader"
ok "leader is $leader"
sync_count=$(roles "$SCOPE-0" | grep -c 'Sync Standby' || true)
[ "$sync_count" = "1" ] || die "expected exactly 1 synchronous standby, found $sync_count"
ok "1 synchronous standby, 1 asynchronous"
streaming=$(roles "$SCOPE-0" | grep -cE 'streaming|running' || true)
[ "$streaming" = "3" ] || die "expected 3 members running/streaming, found $streaming"
ok "every member running or streaming"

step "The application role and database the chart bootstrapped"
[ "$(psql_as_app "$SCOPE-0" 'select current_user')" = "$APP_USER" ] || die "connected as somebody else"
[ "$(psql_as_app "$SCOPE-0" 'select current_database()')" = "$APP_DB" ] || die "connected to another database"
[ "$(psql_as_app "$SCOPE-0" 'select pg_is_in_recovery()')" = "f" ] || die "the master Service resolved to a standby"
ok "$APP_USER connects to $APP_DB through $SCOPE, and it is the primary"

step "A committed row survives the leader's node being taken away"
psql_as_app "$SCOPE-0" 'create table if not exists durability (id int primary key)' >/dev/null
psql_as_app "$SCOPE-0" 'insert into durability values (1) on conflict do nothing' >/dev/null
ok "wrote a row, acknowledged by a synchronous standby"

# Not `patronictl switchover`, which hands over gracefully and is the easy case. This
# is the cordon-then-reboot procedure a TCB update follows, with the reboot replaced by
# the most abrupt thing Kubernetes offers.
#
# The cordon is not decoration. A leader whose pod is force-deleted still holds a lock
# with 30 seconds left on it, and the required one-per-node anti-affinity means the
# replacement pod goes back to the same node — so without the cordon it can come
# straight back, find its own lock, and carry on as leader having never lost anything.
# That is correct behaviour and it tests nothing. Cordoned, the pod stays Pending and a
# standby has to be promoted for the deployment to serve a write at all.
leader_node=$(kubectl get pod -n "$NS" "$leader" -o jsonpath='{.spec.nodeName}')
survivor=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -v "^$leader$" | head -1)
kubectl cordon "$leader_node" >/dev/null
kubectl delete pod -n "$NS" "$leader" --force --grace-period=0 >/dev/null 2>&1
ok "cordoned $leader_node, then destroyed $leader"

printf '  waiting for a new leader'
new_leader=""
for _ in $(seq 1 60); do
  candidate=$(leader_of "$survivor" || true)
  if [ -n "$candidate" ] && [ "$candidate" != "$leader" ]; then
    new_leader="$candidate"
    break
  fi
  printf '.'
  sleep 2
done
printf '\n'
if [ -z "$new_leader" ]; then
  kubectl uncordon "$leader_node" >/dev/null || true
  die "no promotion within 120s; members: $(roles "$survivor" | tr '\n' ' ')"
fi
ok "$leader -> $new_leader"

[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row did not survive the failover"
[ "$(psql_as_app "$survivor" 'select pg_is_in_recovery()')" = "f" ] || die "the master Service still points at a standby"
ok "the row is there, and $SCOPE resolves to the new primary"

step "The destroyed instance re-joins and catches up"
kubectl uncordon "$leader_node" >/dev/null
kubectl wait -n "$NS" --for=condition=ready "pod/$leader" --timeout=600s >/dev/null
roles "$survivor" | sed 's/^/        /'
streaming=$(roles "$survivor" | grep -cE 'streaming|running' || true)
[ "$streaming" = "3" ] || die "expected 3 members running/streaming after the rejoin, found $streaming"
ok "3 members again, all streaming"

step "An instance that comes back with an empty data directory re-clones itself"
# The SUP-179 shape: the node rebooted, its ephemeral volume is gone, and the
# instance has to rebuild from the current leader with nothing of its own to go on.
kubectl delete pvc -n "$NS" "pgdata-$leader" --wait=false >/dev/null
kubectl delete pod -n "$NS" "$leader" --force --grace-period=0 >/dev/null 2>&1
kubectl wait -n "$NS" --for=condition=ready "pod/$leader" --timeout=600s >/dev/null
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row is gone after a re-clone"
rows=$(kubectl exec -n "$NS" "$leader" -c postgresql -- psql -qtAX -U postgres -d "$APP_DB" \
  -c 'select count(*) from durability' | tr -d '[:space:]')
[ "$rows" = "1" ] || die "the re-cloned instance has $rows row(s), expected 1"
ok "$leader rebuilt from the leader and has the data"

step "Result"
printf '  everything passed\n\n'
