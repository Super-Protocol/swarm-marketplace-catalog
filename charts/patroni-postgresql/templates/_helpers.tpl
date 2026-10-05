{{/*
Names.

The full name ignores the release name on purpose. The master Service's name is the
hostname an application has in its DSN, and an application and its database are
installed as two releases of two charts — so a name that moved with the release name
would make every one of those DSNs wrong.
*/}}
{{- define "patroni-postgresql.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The Patroni scope: this cluster's name in the DCS, under which the leader lock and the
cluster configuration are keyed in etcd.

Also the master Service's name, which is now a convention rather than a constraint —
while the lock was an Endpoints object the two had to be one string.
*/}}
{{- define "patroni-postgresql.scope" -}}
{{- default (include "patroni-postgresql.fullname" .) .Values.scope -}}
{{- end -}}

{{/* The DCS and the router: their own names, because their own selectors. */}}
{{- define "patroni-postgresql.etcdName" -}}
{{- printf "%s-etcd" (include "patroni-postgresql.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.etcdFullname" -}}
{{- printf "%s-etcd" (include "patroni-postgresql.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.routerName" -}}
{{- printf "%s-router" (include "patroni-postgresql.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.routerFullname" -}}
{{- printf "%s-router" (include "patroni-postgresql.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* The StatefulSet's governing Service — stable pod DNS, nothing else. */}}
{{- define "patroni-postgresql.headlessServiceName" -}}
{{- printf "%s-pods" (include "patroni-postgresql.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.replicaServiceName" -}}
{{- default (printf "%s-repl" (include "patroni-postgresql.fullname" .)) .Values.service.replica.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "patroni-postgresql.labels" -}}
helm.sh/chart: {{ include "patroni-postgresql.chart" . }}
{{ include "patroni-postgresql.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "patroni-postgresql.selectorLabels" -}}
app.kubernetes.io/name: {{ include "patroni-postgresql.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "patroni-postgresql.podLabels" -}}
{{ include "patroni-postgresql.selectorLabels" . }}
{{- with .Values.podLabels }}
{{ toYaml . | trim }}
{{- end }}
{{- end -}}

{{/*
The DCS's and the router's labels.

Each component carries its own `app.kubernetes.io/name`, not a shared one with a
`component` beside it. That is not a style choice: the PostgreSQL StatefulSet, its
headless Service and its disruption budget all select on name-and-instance, and a
selector is matched against every pod in the namespace — so an etcd pod labelled with
the same name would be a pod the StatefulSet believes it owns.
*/}}
{{- define "patroni-postgresql.etcdSelectorLabels" -}}
app.kubernetes.io/name: {{ include "patroni-postgresql.etcdName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "patroni-postgresql.etcdLabels" -}}
helm.sh/chart: {{ include "patroni-postgresql.chart" . }}
{{ include "patroni-postgresql.etcdSelectorLabels" . }}
app.kubernetes.io/component: dcs
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "patroni-postgresql.routerSelectorLabels" -}}
app.kubernetes.io/name: {{ include "patroni-postgresql.routerName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "patroni-postgresql.routerLabels" -}}
helm.sh/chart: {{ include "patroni-postgresql.chart" . }}
{{ include "patroni-postgresql.routerSelectorLabels" . }}
app.kubernetes.io/component: router
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
A StatefulSet pod's stable name, as an FQDN — with the namespace left as
`$(POD_NAMESPACE)` for whoever reads it to expand.

The namespace is not rendered in on purpose. Every object this chart produces is part
of the snapshot a deployment attests, so a namespace in a manifest is a digest that
differs for every consumer and a version that can therefore never declare an
`expectedDigest` (marketplace spec §2.7). The kubelet expands `$(VAR)` in a container's
environment and arguments, and HAProxy expands `${VAR}` in its own configuration file,
so both readers resolve it at run time without the render having to know.

`podFqdn` writes the kubelet's `$(VAR)`; `podFqdnShell` writes the `${VAR}` that a
shell and HAProxy both read, because `$(...)` in a shell script is a command
substitution and in `haproxy.cfg` is nothing at all.

    (dict "pod" "<name>-0" "service" "<name>-pods" "context" $)
*/}}
{{- define "patroni-postgresql.podFqdn" -}}
{{- printf "%s.%s.$(POD_NAMESPACE).svc.%s" .pod .service .context.Values.clusterDomain -}}
{{- end -}}

{{- define "patroni-postgresql.podFqdnShell" -}}
{{- printf "%s.%s.${POD_NAMESPACE}.svc.%s" .pod .service .context.Values.clusterDomain -}}
{{- end -}}

{{/* etcd's `--initial-cluster`, with the namespace in the form a shell expands. */}}
{{- define "patroni-postgresql.etcdInitialClusterShell" -}}
{{- include "patroni-postgresql.etcdInitialCluster" . | replace "$(POD_NAMESPACE)" "${POD_NAMESPACE}" -}}
{{- end -}}

{{/*
`ETCD3_HOSTS` — every etcd member's client endpoint, comma separated, which is the form
`configure_spilo.py` parses into Patroni's `etcd3.hosts`.

Each member by its own stable name rather than one Service name: Patroni's etcd3 client
holds the list and fails over between them itself, and a headless Service's A records
would hand it whichever addresses DNS happened to cache.
*/}}
{{- define "patroni-postgresql.etcdHosts" -}}
{{- $service := include "patroni-postgresql.etcdFullname" . -}}
{{- $port := .Values.etcd.clientPort | int -}}
{{- $hosts := list -}}
{{- range $i := until (.Values.etcd.replicaCount | int) -}}
{{- $pod := printf "%s-%d" $service $i -}}
{{- $fqdn := include "patroni-postgresql.podFqdn" (dict "pod" $pod "service" $service "context" $) -}}
{{- $hosts = append $hosts (printf "%s:%d" $fqdn $port) -}}
{{- end -}}
{{- join "," $hosts -}}
{{- end -}}

{{/*
etcd's `--initial-cluster`: every member's name and peer URL, which is also what its
cluster id is derived from. It has to be identical on every member and across every
restart — a member that comes back with an empty volume re-derives the same id from
this string and rejoins the cluster it was already part of, which is what makes the
`emptyDir` safe.
*/}}
{{- define "patroni-postgresql.etcdInitialCluster" -}}
{{- $service := include "patroni-postgresql.etcdFullname" . -}}
{{- $port := .Values.etcd.peerPort | int -}}
{{- $peers := list -}}
{{- range $i := until (.Values.etcd.replicaCount | int) -}}
{{- $pod := printf "%s-%d" $service $i -}}
{{- $fqdn := include "patroni-postgresql.podFqdn" (dict "pod" $pod "service" $service "context" $) -}}
{{- $peers = append $peers (printf "%s=http://%s:%d" $pod $fqdn $port) -}}
{{- end -}}
{{- join "," $peers -}}
{{- end -}}

{{/* The Secret the passwords are read from: an existing one, or the one this chart writes. */}}
{{- define "patroni-postgresql.secretName" -}}
{{- default (include "patroni-postgresql.fullname" .) .Values.auth.existingSecret -}}
{{- end -}}

{{- define "patroni-postgresql.configMapName" -}}
{{- printf "%s-scripts" (include "patroni-postgresql.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
An image reference. A digest wins over a tag, and one of the two has to be there: an
unpinned reference would make the deployment's evidence digest uncomputable
(marketplace spec §2.7).

    (dict "image" .Values.etcd.image "values" "etcd.image")
*/}}
{{- define "patroni-postgresql.imageRef" -}}
{{- $image := .image -}}
{{- $repo := printf "%s/%s" $image.registry $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repo $image.digest -}}
{{- else if $image.tag -}}
{{- printf "%s:%s" $repo $image.tag -}}
{{- else -}}
{{- fail (printf "%s.digest is empty and %s.tag is not set: every image in this chart is pinned by digest" .values .values) -}}
{{- end -}}
{{- end -}}

{{- define "patroni-postgresql.image" -}}
{{- include "patroni-postgresql.imageRef" (dict "image" .Values.image "values" "image") -}}
{{- end -}}

{{- define "patroni-postgresql.etcdImage" -}}
{{- include "patroni-postgresql.imageRef" (dict "image" .Values.etcd.image "values" "etcd.image") -}}
{{- end -}}

{{- define "patroni-postgresql.etcdBootstrapImage" -}}
{{- include "patroni-postgresql.imageRef" (dict "image" .Values.etcd.bootstrapImage "values" "etcd.bootstrapImage") -}}
{{- end -}}

{{- define "patroni-postgresql.routerImage" -}}
{{- include "patroni-postgresql.imageRef" (dict "image" .Values.router.image "values" "router.image") -}}
{{- end -}}

{{/* Whether this chart creates the application role and database at bootstrap. */}}
{{- define "patroni-postgresql.hasAppRole" -}}
{{- if .Values.auth.username -}}true{{- end -}}
{{- end -}}

{{/*
`SPILO_CONFIGURATION` — the Patroni configuration this chart decides, as the YAML
Spilo merges over its own template (`configure_spilo.py`; the user's value wins for
every scalar).

`bootstrap.dcs` is written to the DCS once, when the cluster is initialised. Editing
it later is not a reconfigure of a running cluster — that is `patronictl edit-config`.

The callbacks are here because they are the one thing `configure_spilo.py` will not
let the environment decide. Running under Kubernetes with a DCS that is not the
Kubernetes API, it assigns `CALLBACK_SCRIPT = callback_role.py` unconditionally,
overwriting whatever was passed in — the Postgres operator's arrangement, where a
pod's role label is what a selector Service follows. Here that script has nothing to
talk to: it would PATCH the pod through an API server the confinement blocks, retry
ten times over roughly eight minutes, and delay the post-promotion hook it is chained
in front of by exactly that long, every promotion. So the two labelling callbacks
become `/bin/true`, and `on_role_change` becomes what Spilo itself renders when there
is no callback script — `on_role_change.sh`, which is what runs Spilo's `post_init.sh`
and the post-promotion `vacuumdb`, with `true` where the labelling would have been.
*/}}
{{- define "patroni-postgresql.spiloConfiguration" -}}
{{- $config := dict
  "postgresql" (dict
    "callbacks" (dict
      "on_start" "/bin/true"
      "on_stop" "/bin/true"
      "on_role_change" (printf "/scripts/on_role_change.sh %s true" (include "patroni-postgresql.humanRole" .))
    )
  )
  "bootstrap" (dict
    "dcs" (dict
      "ttl" (.Values.patroni.ttl | int)
      "loop_wait" (.Values.patroni.loopWait | int)
      "retry_timeout" (.Values.patroni.retryTimeout | int)
      "maximum_lag_on_failover" (.Values.patroni.maximumLagOnFailover | int)
      "synchronous_mode" .Values.patroni.synchronousMode
      "synchronous_mode_strict" .Values.patroni.synchronousModeStrict
      "failsafe_mode" .Values.patroni.failsafeMode
    )
  )
-}}
{{- if .Values.patroni.synchronousMode -}}
{{- $_ := set (index $config "bootstrap" "dcs") "synchronous_node_count" (.Values.patroni.synchronousNodeCount | int) -}}
{{- end -}}
{{- if include "patroni-postgresql.hasAppRole" . -}}
{{- $_ := set (index $config "bootstrap") "post_init" (printf "/etc/patroni/scripts/post-init.sh %s" (include "patroni-postgresql.humanRole" .)) -}}
{{- end -}}
{{- toYaml (mergeOverwrite $config (deepCopy .Values.patroni.spiloConfiguration)) -}}
{{- end -}}

{{/*
Spilo's `HUMAN_ROLE` — the group it grants read access to for people who log in
through its PAM/OAuth2 path. Nothing here configures that path, so the role is
created and never used; the name is Spilo's default, kept so the generated
`post_init.sh` runs against the arguments it expects.
*/}}
{{- define "patroni-postgresql.humanRole" -}}zalandos{{- end -}}

{{/*
Configuration mistakes that are cheaper to fail here than in a crash loop.
*/}}
{{- define "patroni-postgresql.validate" -}}
{{- $replicas := .Values.replicaCount | int -}}
{{- if lt $replicas 1 -}}
{{- fail "replicaCount must be at least 1" -}}
{{- end -}}
{{- if and .Values.patroni.synchronousMode (lt $replicas 2) -}}
{{- fail "patroni.synchronousMode is on with replicaCount 1: there is no standby for a commit to be durable on. Set replicaCount to 3, or turn synchronous mode off and say out loud that this deployment can lose data" -}}
{{- end -}}
{{- if and .Values.patroni.synchronousMode (ge (.Values.patroni.synchronousNodeCount | int) $replicas) -}}
{{- fail (printf "patroni.synchronousNodeCount is %d with replicaCount %d: a leader cannot be its own standby, so every write would block forever" (.Values.patroni.synchronousNodeCount | int) $replicas) -}}
{{- end -}}
{{- if and .Values.patroni.synchronousModeStrict (not .Values.patroni.synchronousMode) -}}
{{- fail "patroni.synchronousModeStrict is on while patroni.synchronousMode is off: strict mode is a property of synchronous replication, and on its own it configures nothing" -}}
{{- end -}}
{{- if not .Values.auth.existingSecret -}}
{{- if not .Values.auth.superuser.password -}}
{{- fail "auth.superuser.password must be set, or auth.existingSecret must name a Secret holding it: it is the credential Patroni itself connects with" -}}
{{- end -}}
{{- if not .Values.auth.replication.password -}}
{{- fail "auth.replication.password must be set, or auth.existingSecret must name a Secret holding it: without it a standby cannot stream from the leader" -}}
{{- end -}}
{{- if and .Values.auth.username (not .Values.auth.password) -}}
{{- fail "auth.username asks for an application role but auth.password is empty: set it, or point auth.existingSecret at a Secret that holds it" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.auth.username (not .Values.auth.database) -}}
{{- fail "auth.username is set but auth.database is empty: the application role is created together with the database it owns, and a role with nothing to connect to is not useful" -}}
{{- end -}}
{{- if and .Values.auth.database (not .Values.auth.username) -}}
{{- fail "auth.database is set but auth.username is empty: the database is created owned by the application role, so the two go together" -}}
{{- end -}}
{{/*
Both names are interpolated into the SQL the post-init hook runs, so both are held to
what an unquoted PostgreSQL identifier may be. A name that needed quoting is not
refused because it could not work — it is refused because making it work means
trusting this chart's escaping with a shell, a here-document and psql in the path.
*/}}
{{- range $field, $value := dict "auth.username" .Values.auth.username "auth.database" .Values.auth.database -}}
{{- if and $value (not (regexMatch "^[a-z_][a-z0-9_]{0,62}$" $value)) -}}
{{- fail (printf "%s is %q: lowercase letters, digits and underscores only, not starting with a digit" $field $value) -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.auth.superuser.username .Values.auth.replication.username -}}
{{- fail "auth.superuser.username and auth.replication.username are the same role: PostgreSQL would be streaming replication as its own superuser" -}}
{{- end -}}
{{- if and .Values.auth.username (eq .Values.auth.username .Values.auth.superuser.username) -}}
{{- fail "auth.username is the superuser's name: the application would connect with unrestricted rights to every database in the cluster" -}}
{{- end -}}
{{- if and .Values.podAntiAffinity.enabled .Values.affinity.podAntiAffinity -}}
{{- fail "affinity.podAntiAffinity is set while podAntiAffinity.enabled is true: the chart's required one-per-node rule would be the one that applies. Turn podAntiAffinity.enabled off to replace it" -}}
{{- end -}}
{{- if and .Values.podDisruptionBudget.enabled (ge (.Values.podDisruptionBudget.maxUnavailable | int) $replicas) -}}
{{- fail (printf "podDisruptionBudget.maxUnavailable is %d of %d instances: a budget that permits the whole cluster to go at once is not a budget" (.Values.podDisruptionBudget.maxUnavailable | int) $replicas) -}}
{{- end -}}
{{/*
The DCS and the router, which are as able to make the database unreachable as the
database is.
*/}}
{{- $etcd := .Values.etcd.replicaCount | int -}}
{{- if lt $etcd 1 -}}
{{- fail "etcd.replicaCount must be at least 1" -}}
{{- end -}}
{{- if eq (mod $etcd 2) 0 -}}
{{- fail (printf "etcd.replicaCount is %d: an even number of members has a quorum of %d, so it tolerates no more failures than %d members would and is strictly worse than an odd count. Use 1, 3 or 5" $etcd (add (div $etcd 2) 1) (sub $etcd 1)) -}}
{{- end -}}
{{- if and .Values.etcd.podDisruptionBudget.enabled (gt $etcd 1) (ge (.Values.etcd.podDisruptionBudget.maxUnavailable | int) (sub $etcd (div $etcd 2))) -}}
{{- fail (printf "etcd.podDisruptionBudget.maxUnavailable is %d of %d members: a drain permitted to take the quorum is a leader lock nobody can renew, and a database that demotes itself while every instance of it is healthy" (.Values.etcd.podDisruptionBudget.maxUnavailable | int) $etcd) -}}
{{- end -}}
{{- $router := .Values.router.replicaCount | int -}}
{{- if lt $router 1 -}}
{{- fail "router.replicaCount must be at least 1: the router is the address the application connects to, and there is no path around it" -}}
{{- end -}}
{{- if and .Values.router.podDisruptionBudget.enabled (ge (.Values.router.podDisruptionBudget.maxUnavailable | int) $router) -}}
{{- fail (printf "router.podDisruptionBudget.maxUnavailable is %d of %d instances: a drain permitted to take every router is a database nothing can reach" (.Values.router.podDisruptionBudget.maxUnavailable | int) $router) -}}
{{- end -}}
{{- if eq (.Values.router.primaryPort | int) (.Values.router.standbyPort | int) -}}
{{- fail (printf "router.primaryPort and router.standbyPort are both %d: the two listeners are in one pod and cannot bind the same port" (.Values.router.primaryPort | int)) -}}
{{- end -}}
{{- if eq (.Values.router.healthPort | int) (.Values.router.primaryPort | int) -}}
{{- fail (printf "router.healthPort and router.primaryPort are both %d: the two listeners are in one pod and cannot bind the same port" (.Values.router.healthPort | int)) -}}
{{- end -}}
{{- if eq (.Values.router.healthPort | int) (.Values.router.standbyPort | int) -}}
{{- fail (printf "router.healthPort and router.standbyPort are both %d: the two listeners are in one pod and cannot bind the same port" (.Values.router.healthPort | int)) -}}
{{- end -}}
{{- if not .Values.clusterDomain -}}
{{- fail "clusterDomain is empty: every name in the DCS configuration and the router configuration is an FQDN built against it" -}}
{{- end -}}
{{- end -}}
