{{/*
Names.

The fullname ignores the release name on purpose, twice over. Five workloads have
to find each other by DNS inside one namespace — the console's `API_BASE_URL`,
the gateway's engine endpoint, the Job's admin URL — and a deployment's evidence
digest is taken over the objects it rendered, so a name that moved with the
release would make the digest a property of the release name rather than of the
version being attested.
*/}}
{{- define "confidential-s3.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-s3.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-s3.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-s3.labels" -}}
helm.sh/chart: {{ include "confidential-s3.chart" . }}
app.kubernetes.io/name: {{ include "confidential-s3.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: confidential-s3
{{- end -}}

{{/* Per-component selector labels. `$component` is the short name: gateway, api… */}}
{{- define "confidential-s3.selectorLabels" -}}
app.kubernetes.io/name: {{ include "confidential-s3.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "confidential-s3.componentLabels" -}}
{{ include "confidential-s3.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "confidential-s3.componentName" -}}
{{- printf "%s-%s" (include "confidential-s3.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* The Secret this chart creates. Never one a value points at. */}}
{{- define "confidential-s3.secretName" -}}
{{- include "confidential-s3.fullname" . -}}
{{- end -}}

{{/*
An image reference. A digest wins over a tag, and one of the two has to be there:
an unpinned reference makes a deployment's evidence digest uncomputable
(marketplace spec §2.7), so the chart refuses rather than pulls something.

Takes a dict of `image` and `name`, the latter only to say which one is wrong.
*/}}
{{- define "confidential-s3.image" -}}
{{- $image := .image -}}
{{- $repo := printf "%s/%s" $image.registry $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repo $image.digest -}}
{{- else if $image.tag -}}
{{- printf "%s:%s" $repo $image.tag -}}
{{- else -}}
{{- fail (printf "%s.image.digest is empty and %s.image.tag is not set: every image in this chart is pinned by digest" .name .name) -}}
{{- end -}}
{{- end -}}

{{- define "confidential-s3.consoleUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "consoleHostname must be set" .Values.consoleHostname) -}}
{{- end -}}

{{- define "confidential-s3.s3Url" -}}
{{- printf "%s://%s" .Values.publicScheme (required "s3Hostname must be set" .Values.s3Hostname) -}}
{{- end -}}

{{/* The bundled database's service, unless an external host was given. */}}
{{- define "confidential-s3.databaseHost" -}}
{{- if .Values.database.host -}}
{{- .Values.database.host -}}
{{- else if .Values.postgresql.enabled -}}
{{- .Values.postgresql.fullnameOverride -}}
{{- else -}}
{{- fail "database.host must be set when postgresql.enabled is false" -}}
{{- end -}}
{{- end -}}

{{/*
--------------------------------------------------------------------------------
Derived credentials.

Garage imports an access key of exactly one shape and no other — `GK` plus 24 hex
characters, then 64 hex characters, and a 400 for anything else. The control
plane loads a master key of exactly 32 bytes. A marketplace `generated` parameter
is a random string with no shape at all.

So the shapes are derived here, from seeds, with SHA-256 and one domain separator
each. That is what lets a single generated parameter produce a value both halves
of the deployment compute identically — the gateway from its Secret, the Job from
the same Secret — without a credential being written down anywhere or a Job
having to mint one. A published cluster space is frozen: a Job that generated a
secret would have nowhere to put it.
--------------------------------------------------------------------------------
*/}}
{{- define "confidential-s3.masterKey" -}}
{{- if .Values.masterKey.value -}}
{{- .Values.masterKey.value -}}
{{- else -}}
{{- sha256sum (required "masterKey.seed or masterKey.value must be set" .Values.masterKey.seed) -}}
{{- end -}}
{{- end -}}

{{- define "confidential-s3.engineAccessKey" -}}
{{- if .Values.engine.accessKey -}}
{{- .Values.engine.accessKey -}}
{{- else -}}
{{- printf "GK%s" (sha256sum (printf "confidential-s3/engine-access-key|%s" (required "engine.seed must be set" .Values.engine.seed)) | trunc 24) -}}
{{- end -}}
{{- end -}}

{{- define "confidential-s3.engineSecretKey" -}}
{{- if .Values.engine.secretKey -}}
{{- .Values.engine.secretKey -}}
{{- else -}}
{{- sha256sum (printf "confidential-s3/engine-secret-key|%s" (required "engine.seed must be set" .Values.engine.seed)) -}}
{{- end -}}
{{- end -}}

{{- define "confidential-s3.garageRpcSecret" -}}
{{- sha256sum (printf "confidential-s3/garage-rpc-secret|%s" (required "engine.seed must be set" .Values.engine.seed)) -}}
{{- end -}}

{{- define "confidential-s3.garageAdminToken" -}}
{{- sha256sum (printf "confidential-s3/garage-admin-token|%s" (required "engine.seed must be set" .Values.engine.seed)) -}}
{{- end -}}

{{/*
--------------------------------------------------------------------------------
Configuration checks that are cheaper to fail here than in a crash loop. Each one
below is a mistake that deploys cleanly and then does not work.
--------------------------------------------------------------------------------
*/}}
{{- define "confidential-s3.validate" -}}
{{- if not .Values.consoleHostname -}}
{{- fail "consoleHostname must be set: it is where the console is served and the origin the browser signs in against" -}}
{{- end -}}
{{- if not .Values.s3Hostname -}}
{{- fail "s3Hostname must be set: it is the endpoint URL an S3 client is pointed at" -}}
{{- end -}}
{{- if eq .Values.consoleHostname .Values.s3Hostname -}}
{{- fail "consoleHostname and s3Hostname must differ: they are two ingresses, for two different services" -}}
{{- end -}}

{{- if and .Values.masterKey.value .Values.masterKey.seed -}}
{{- fail "masterKey.value and masterKey.seed are both set: one of them is the key this deployment will use and the other is ignored, which is not a state to deploy in" -}}
{{- end -}}
{{- if .Values.masterKey.value -}}
{{- if not (or (regexMatch "^[0-9a-fA-F]{64}$" .Values.masterKey.value) (regexMatch "^[A-Za-z0-9+/_-]{43}=?$" .Values.masterKey.value)) -}}
{{- fail "masterKey.value must decode to 32 bytes: 64 hex characters, or 43 base64 characters. Leave it empty and set masterKey.seed to have one derived" -}}
{{- end -}}
{{- else if not .Values.masterKey.existingSecret -}}
{{- if lt (len (default "" .Values.masterKey.seed)) 32 -}}
{{- fail "masterKey.seed must be at least 32 characters. It is what the 32-byte key is derived from; the same key opens every registered back storage and every encrypted bucket, and a deployment whose two halves disagree on it cannot read its own data" -}}
{{- end -}}
{{- end -}}

{{- if or .Values.engine.accessKey .Values.engine.secretKey -}}
{{- if not (and .Values.engine.accessKey .Values.engine.secretKey) -}}
{{- fail "engine.accessKey and engine.secretKey go together: set both, or neither and let engine.seed derive them" -}}
{{- end -}}
{{- if not (regexMatch "^GK[0-9a-f]{24}$" .Values.engine.accessKey) -}}
{{- fail "engine.accessKey is not a Garage key id: GK followed by 24 lowercase hex characters. Garage refuses anything else, which is a deployment that comes up and cannot store a byte" -}}
{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" .Values.engine.secretKey) -}}
{{- fail "engine.secretKey is not a Garage secret key: 64 lowercase hex characters" -}}
{{- end -}}
{{- else if lt (len (default "" .Values.engine.seed)) 32 -}}
{{- fail "engine.seed must be at least 32 characters. The gateway's credential on the engine, the engine's RPC secret and its admin token are all derived from it" -}}
{{- end -}}

{{- if not (or .Values.bootstrapToken.value .Values.bootstrapToken.existingSecret) -}}
{{- fail "bootstrapToken.value must be set: without it nobody can claim the administrator account, and this deployment has no mailbox to send an invitation from" -}}
{{- end -}}
{{- if not .Values.api.bootstrap.adminEmail -}}
{{- fail "api.bootstrap.adminEmail must be set: it is the address the first administrator account is created under" -}}
{{- end -}}
{{- if not (contains "@" .Values.api.bootstrap.adminEmail) -}}
{{- fail (printf "api.bootstrap.adminEmail must be an email address, not %q" .Values.api.bootstrap.adminEmail) -}}
{{- end -}}

{{- if and .Values.database.url .Values.postgresql.enabled -}}
{{- fail "database.url points at an external database, but postgresql.enabled is true: set postgresql.enabled to false" -}}
{{- end -}}
{{- if and .Values.database.existingSecret .Values.postgresql.enabled -}}
{{- fail "database.existingSecret points at an external database, but postgresql.enabled is true: set postgresql.enabled to false" -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (not .Values.database.password) -}}
{{- fail "database.password must be set: it is both what the bundled PostgreSQL is created with and what the control plane and the gateway connect with" -}}
{{- end -}}
{{- if not (or .Values.postgresql.enabled .Values.database.url .Values.database.password .Values.database.existingSecret) -}}
{{- fail "postgresql.enabled is false: set database.url, or database.password, or database.existingSecret" -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (ne .Values.postgresql.auth.existingSecret (include "confidential-s3.fullname" .)) -}}
{{- fail (printf "postgresql.auth.existingSecret must be %q — the Secret this chart creates — or the server and its two clients will disagree on the password" (include "confidential-s3.fullname" .)) -}}
{{- end -}}

{{- if and .Values.virtualHostStyle (not .Values.s3Hostname) -}}
{{- fail "virtualHostStyle needs s3Hostname" -}}
{{- end -}}
{{- if and .Values.gateway.ingress.enabled (not .Values.gateway.ingress.className) -}}
{{- fail "gateway.ingress.className must be set: an Ingress with no class is created, has its hostname registered and its DNS written, and is served by nothing" -}}
{{- end -}}
{{- if and .Values.console.ingress.enabled (not .Values.console.ingress.className) -}}
{{- fail "console.ingress.className must be set: an Ingress with no class is created, has its hostname registered and its DNS written, and is served by nothing" -}}
{{- end -}}
{{- $chunk := .Values.gateway.chunkSize | int64 -}}
{{- if or (lt $chunk 65536) (gt $chunk 67108864) -}}
{{- fail (printf "gateway.chunkSize must be between 65536 and 67108864 bytes, not %d" $chunk) -}}
{{- end -}}
{{- end -}}

{{/*
The capacity the engine's node advertises in its layout, in bytes.

Derived from the data volume rather than asked for separately: a node that
advertises more than its volume holds fills the volume and stops, and a node that
advertises less leaves the difference unused. Two values that have to agree and
are set apart from each other eventually do not.
*/}}
{{- define "confidential-s3.garageCapacityBytes" -}}
{{- $size := .Values.garage.persistence.data.size | toString -}}
{{- $units := dict "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 "K" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 -}}
{{- $bytes := 0 -}}
{{- range $suffix, $multiplier := $units -}}
{{- if and (eq $bytes 0) (hasSuffix $suffix $size) -}}
{{- $bytes = mul (trimSuffix $suffix $size | int64) $multiplier -}}
{{- end -}}
{{- end -}}
{{- if eq $bytes 0 -}}
{{- $bytes = $size | int64 -}}
{{- end -}}
{{- if le ($bytes | int64) 0 -}}
{{- fail (printf "garage.persistence.data.size is %q, which is not a size the layout capacity can be derived from" $size) -}}
{{- end -}}
{{- $bytes -}}
{{- end -}}
