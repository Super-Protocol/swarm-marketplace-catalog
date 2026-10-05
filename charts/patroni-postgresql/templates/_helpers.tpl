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
The Patroni scope.

Also the name of the leader-lock Endpoints object, and therefore the name of the
master Service: in Endpoints mode Patroni writes the leader's address into the
Endpoints named after the scope, and a Service only picks that up if it is the
Service that owns that name.
*/}}
{{- define "patroni-postgresql.scope" -}}
{{- default (include "patroni-postgresql.fullname" .) .Values.scope -}}
{{- end -}}

{{/* The second DCS object: `<scope>-config` holds the cluster-wide Patroni config. */}}
{{- define "patroni-postgresql.configServiceName" -}}
{{- printf "%s-config" (include "patroni-postgresql.scope" .) | trunc 63 | trimSuffix "-" -}}
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

{{/*
The labels Patroni finds its members by: whatever `patroni.labels` says, plus the
scope under the scope label. Patroni lists pods with exactly this selector, so a pod
missing one of them is a pod the cluster does not know exists — no leader, no
failover, and nothing in the log that says why.
*/}}
{{- define "patroni-postgresql.dcsLabels" -}}
{{ .Values.patroni.scopeLabel }}: {{ include "patroni-postgresql.scope" . | quote }}
{{- range $key, $value := .Values.patroni.labels }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end -}}

{{- define "patroni-postgresql.podLabels" -}}
{{ include "patroni-postgresql.selectorLabels" . }}
{{ include "patroni-postgresql.dcsLabels" . }}
{{- with .Values.podLabels }}
{{ toYaml . | trim }}
{{- end }}
{{- end -}}

{{- define "patroni-postgresql.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "patroni-postgresql.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* The Secret the passwords are read from: an existing one, or the one this chart writes. */}}
{{- define "patroni-postgresql.secretName" -}}
{{- default (include "patroni-postgresql.fullname" .) .Values.auth.existingSecret -}}
{{- end -}}

{{- define "patroni-postgresql.configMapName" -}}
{{- printf "%s-scripts" (include "patroni-postgresql.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The image reference. A digest wins over a tag, and one of the two has to be there:
an unpinned reference would make the deployment's evidence digest uncomputable
(marketplace spec §2.7).
*/}}
{{- define "patroni-postgresql.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
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
*/}}
{{- define "patroni-postgresql.spiloConfiguration" -}}
{{- $config := dict
  "bootstrap" (dict
    "dcs" (dict
      "ttl" (.Values.patroni.ttl | int)
      "loop_wait" (.Values.patroni.loopWait | int)
      "retry_timeout" (.Values.patroni.retryTimeout | int)
      "maximum_lag_on_failover" (.Values.patroni.maximumLagOnFailover | int)
      "synchronous_mode" .Values.patroni.synchronousMode
      "synchronous_mode_strict" .Values.patroni.synchronousModeStrict
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
{{- if not (eq (.Values.service.port | int) 5432) -}}
{{- fail "service.port must be 5432: Patroni writes the leader Endpoints with the port Spilo hardcodes for it, and a Service port that disagreed would resolve to nothing" -}}
{{- end -}}
{{- end -}}
