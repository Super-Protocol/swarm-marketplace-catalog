#!/usr/bin/env bash
#
# What a golden render cannot tell you about a database: whether it elects a leader,
# whether a standby clones itself from that leader, whether the master Service follows
# a failover, whether the store the election happens in can be thrown away, and whether
# anything was lost on the way.
#
# It runs with **pod → kube-apiserver blocked**, because that is the condition a
# cluster space imposes and the one the 0.1.0 chart could not bootstrap behind
# (SUP-215). Under that block the Kubernetes-DCS version of this chart sits at 0/3
# forever; this is the run that says the etcd version does not.
#
#   charts/tests/smoke/patroni.sh
#   KEEP=1 charts/tests/smoke/patroni.sh     # leave the cluster up afterwards
#
# Needs: kind, kubectl, helm, docker. Every image is public, so unlike the router
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

# Every image the chart pins, read out of its own values so this cannot drift from
# what a deployment would pull.
read -r SPILO_IMAGE ETCD_IMAGE BOOTSTRAP_IMAGE ROUTER_IMAGE <<<"$(
  python3 - <<'PY'
import yaml
values = yaml.safe_load(open("charts/patroni-postgresql/values.yaml"))
def ref(image):
    return f"{image['registry']}/{image['repository']}@{image['digest']}"
print(ref(values["image"]), ref(values["etcd"]["image"]),
      ref(values["etcd"]["bootstrapImage"]), ref(values["router"]["image"]))
PY
)"
IMAGES=("$SPILO_IMAGE" "$ETCD_IMAGE" "$BOOTSTRAP_IMAGE" "$ROUTER_IMAGE")

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

cleanup() {
  local code=$?
  if [ $code -ne 0 ]; then
    printf '\n--- pods ---\n' >&2
    kubectl get pods -n "$NS" -o wide >&2 || true
    kubectl logs -n "$NS" "$SCOPE-0" --tail=80 >&2 || true
    printf '\n--- dcs ---\n' >&2
    kubectl logs -n "$NS" "$SCOPE-etcd-0" --all-containers --tail=40 >&2 || true
    printf '\n--- router ---\n' >&2
    kubectl logs -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql-router" --tail=40 >&2 || true
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

# How long the master Service takes to mean the new primary, which is not the same as
# how long Patroni takes to promote one.
#
# The router finds out by health check — `router.check.interval × rise` — and then its
# own readiness has to pass and the Service's endpoints have to follow. Until all of
# that lands the name has no endpoints at all and a connection to it is *refused*,
# which is the readiness probe's whole purpose: a client that retries, rather than one
# handed a connection to a standby that rejects its writes.
await_primary() {
  local pod=$1 waited=0
  while [ "$waited" -lt 120 ]; do
    if [ "$(psql_as_app "$pod" 'select pg_is_in_recovery()' 2>/dev/null)" = "f" ]; then
      printf '  %2ss  for %s to resolve to the primary again\n' "$waited" "$SCOPE"
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

step "Images"
for image in "${IMAGES[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image" >/dev/null
  ok "$image"
done

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
  for image in "${IMAGES[@]}"; do
    docker exec "$node" crictl pull "$image" >/dev/null &
  done
done
wait
kubectl create namespace "$NS" >/dev/null
ok "3 nodes, every image present on each"

step "Confinement: pod → kube-apiserver blocked on every worker"
# The condition this chart exists to work under. A cluster space confines its tenant
# pods and this is one of the things the confinement blocks; the Kubernetes-DCS chart
# deployed cleanly behind it and never elected anybody, with `OrderedReady` holding the
# StatefulSet at 0/3 and everything downstream waiting forever (SUP-215).
#
# Simulated in the one place that is faithful to it: the workers' FORWARD chain, which
# pod traffic crosses and host-network traffic does not. The kubelet, kube-proxy and
# kindnet all talk to the API server out of the host's own network namespace and are
# untouched; `kubectl exec` still works, because that arrives from the API server.
#
# The two pieces of kube-system that *are* ordinary pods and do need the API server —
# CoreDNS and the local-path provisioner — are moved to the control-plane node first,
# which is not confined. In a real cluster space they are on the platform's side of the
# boundary for the same reason.
for workload in kube-system/coredns local-path-storage/local-path-provisioner; do
  kubectl -n "${workload%%/*}" patch deployment "${workload##*/}" --type merge -p '{
    "spec": {"template": {"spec": {
      "nodeSelector": {"node-role.kubernetes.io/control-plane": ""},
      "tolerations": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}]
    }}}
  }' >/dev/null
  kubectl -n "${workload%%/*}" rollout status "deployment/${workload##*/}" --timeout=180s >/dev/null
done
ok "CoreDNS and the local-path provisioner are on the control-plane node"

apiserver_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CLUSTER-control-plane")
for node in $(kind get nodes --name "$CLUSTER" | grep -v control-plane); do
  docker exec "$node" iptables -I FORWARD 1 -p tcp -d "$apiserver_ip" --dport 6443 -j DROP
  docker exec "$node" iptables -I FORWARD 1 -p tcp -d 10.96.0.1 --dport 443 -j DROP
done
ok "a pod on a worker cannot reach $apiserver_ip:6443, and times out rather than being refused"

step "Install"
helm install "$RELEASE" charts/patroni-postgresql --namespace "$NS" \
  --set etcd.resources.requests.cpu=50m --set etcd.resources.limits.cpu=200m \
  --set router.resources.requests.cpu=50m --set router.resources.limits.cpu=200m \
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

step "The DCS comes up, one member per node"
kubectl rollout status -n "$NS" "statefulset/$SCOPE-etcd" --timeout=300s >/dev/null
etcd_nodes=$(kubectl get pods -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql-etcd" \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u | wc -l)
[ "$etcd_nodes" = "3" ] || die "the DCS's 3 members are on $etcd_nodes node(s)"
ok "3 etcd members, 3 nodes"
# What the chart claims, checked rather than asserted: the pods have no token, and the
# API server is not reachable from inside them even if they had one.
bootstrap_modes=$(kubectl logs -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql-etcd" \
  -c dcs-bootstrap --tail=-1 | grep -c 'initial-cluster-state=new' || true)
[ "$bootstrap_modes" = "3" ] || die "expected 3 members to bootstrap a new cluster, $bootstrap_modes did"
ok "every member bootstrapped the cluster rather than tried to join one"

step "Nothing in the deployment can reach the API server"
# The claim the whole change rests on. `/dev/tcp` because the probe has to come from
# inside the pod's own network namespace, and bash is what is in the image; `exec`
# rather than a read, so that a connection which succeeds returns instead of waiting
# for a server that has nothing to say until it is asked.
connects() { kubectl exec -n "$NS" "$SCOPE-0" -c postgresql -- timeout 5 bash -c "exec 3<>/dev/tcp/$1"; }
if connects 10.96.0.1/443 >/dev/null 2>&1; then
  die "the database pod reached the API server: the confinement is not in place and this run would prove nothing"
fi
ok "10.96.0.1:443 times out from inside the database pod"

# Retried, because the first few seconds after an install are the one time this name
# legitimately does not resolve: Patroni looks the members up before their pods have
# addresses, and CoreDNS caches the denial for 30 seconds. It is also why the DCS's own
# log opens with a page of "no such host" and then forms a cluster anyway.
dcs_reachable=
for _ in $(seq 1 20); do
  if connects "$SCOPE-etcd-0.$SCOPE-etcd.$NS.svc.cluster.local/2379" >/dev/null 2>&1; then
    dcs_reachable=yes
    break
  fi
  sleep 3
done
[ -n "$dcs_reachable" ] || die "the database pod cannot reach its own DCS either, so the block is too broad"
ok "the DCS answers on the same namespace's network, which is the traffic that matters"
if kubectl exec -n "$NS" "$SCOPE-0" -c postgresql -- \
     test -f /var/run/secrets/kubernetes.io/serviceaccount/token 2>/dev/null; then
  die "the database pod has a service account token mounted"
fi
ok "no service account token in the pod"

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

step "The router is what the master Service resolves to"
kubectl rollout status -n "$NS" "deployment/$SCOPE-router" --timeout=300s >/dev/null
router_endpoints=$(kubectl get endpoints -n "$NS" "$SCOPE" \
  -o jsonpath='{range .subsets[*].addresses[*]}{.ip}{"\n"}{end}' | wc -l)
[ "$router_endpoints" = "2" ] || die "the master Service has $router_endpoints endpoint(s), expected the 2 routers"
ok "2 router instances behind $SCOPE"

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

await_primary "$survivor" || die "$SCOPE never resolved to a primary after the promotion"
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row did not survive the failover"
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
await_primary "$survivor" || die "$SCOPE stopped resolving to a primary while an instance re-cloned"
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row is gone after a re-clone"
rows=$(kubectl exec -n "$NS" "$leader" -c postgresql -- psql -qtAX -U postgres -d "$APP_DB" \
  -c 'select count(*) from durability' | tr -d '[:space:]')
[ "$rows" = "1" ] || die "the re-cloned instance has $rows row(s), expected 1"
ok "$leader rebuilt from the leader and has the data"

step "One DCS member thrown away comes back"
# The routine case: a rolling update, a drained node, an evicted pod. The member's
# volume is an `emptyDir`, so it comes back with nothing — and has to be re-admitted to
# a cluster that still lists it, which is the whole reason the bootstrap script exists.
kubectl delete pod -n "$NS" "$SCOPE-etcd-1" --force --grace-period=0 >/dev/null 2>&1
kubectl wait -n "$NS" --for=condition=ready "pod/$SCOPE-etcd-1" --timeout=300s >/dev/null
joined=$(kubectl logs -n "$NS" "$SCOPE-etcd-1" -c dcs-bootstrap --tail=-1 | grep -c 'initial-cluster-state=existing' || true)
[ "$joined" = "1" ] || die "$SCOPE-etcd-1 did not join the existing cluster: $(kubectl logs -n "$NS" "$SCOPE-etcd-1" -c dcs-bootstrap --tail=5 | tr '\n' ' ')"
ok "it joined the cluster it was already a member of, with an empty volume"
await_primary "$survivor" || die "$SCOPE stopped resolving to a primary when one DCS member was replaced"
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row did not survive a DCS member restart"
ok "the database never noticed"

step "The whole DCS thrown away comes back, and the database keeps its leader"
# The cloud-wide reboot: every node's disk is wiped, so every member's volume goes with
# it and the leader lock, the member list and the cluster configuration are all gone.
# None of it is data — it is all re-derivable from the instances that still have the
# database — which is the claim being tested here.
leader_before=$(leader_of "$survivor")
kubectl delete pod -n "$NS" -l "app.kubernetes.io/name=patroni-postgresql-etcd" \
  --force --grace-period=0 >/dev/null 2>&1
kubectl rollout status -n "$NS" "statefulset/$SCOPE-etcd" --timeout=300s >/dev/null
ok "3 members again, from nothing"
printf '  waiting for the cluster to re-register'
for _ in $(seq 1 60); do
  members=$(roles "$survivor" | wc -l || true)
  [ "$members" = "3" ] && break
  printf '.'
  sleep 2
done
printf '\n'
roles "$survivor" | sed 's/^/        /'
[ "$(roles "$survivor" | wc -l)" = "3" ] || die "the cluster did not re-register in the new DCS"
leader_after=$(leader_of "$survivor")
[ -n "$leader_after" ] || die "no leader after the DCS was replaced"
[ "$leader_after" = "$leader_before" ] || die "the DCS being replaced caused a failover: $leader_before -> $leader_after"
ok "$leader_after is still the leader — failsafe mode held the lock while the DCS was gone"
await_primary "$survivor" || die "$SCOPE never resolved to a primary after the DCS was replaced"
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "1" ] || die "the row did not survive the DCS being replaced"
ok "the row is there and $SCOPE still resolves to the primary"

step "A write still lands, after all of it"
psql_as_app "$survivor" 'insert into durability values (2) on conflict do nothing' >/dev/null
[ "$(psql_as_app "$survivor" 'select count(*) from durability')" = "2" ] || die "the cluster cannot take a write"
ok "2 rows"

step "Result"
printf '  everything passed\n\n'
