{{/*
Names.

The fullname ignores the release name on purpose. Three charts deploy this
listing and they have to find each other by DNS: the litellm chart's service is
what `litellmUrl` defaults to, and the console's ingress routes to this chart's
service. Names that moved with the release name would make every one of those
defaults wrong.
*/}}
{{- define "confidential-router-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-api.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-api.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-api.labels" -}}
helm.sh/chart: {{ include "confidential-router-api.chart" . }}
{{ include "confidential-router-api.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: confidential-router
{{- end -}}

{{- define "confidential-router-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "confidential-router-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
The image reference. A digest wins over a tag, and one of the two has to be
there: an unpinned `:latest` would make the deployment's evidence digest
uncomputable (marketplace spec §2.7).
*/}}
{{- define "confidential-router-api.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{/*
NODE_ENV. The manual payment provider mints credit from a signed link, so the
API refuses to bind it under production (ADR-005 §4) — `billing.mode: manual`
therefore has to run outside production mode, and says so here rather than
crash-looping in the cluster.
*/}}
{{- define "confidential-router-api.nodeEnv" -}}
{{- if .Values.nodeEnv -}}
{{- .Values.nodeEnv -}}
{{- else if eq .Values.billing.mode "stripe" -}}
production
{{- else -}}
development
{{- end -}}
{{- end -}}

{{- define "confidential-router-api.apiUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "apiHostname must be set" .Values.apiHostname) -}}
{{- end -}}

{{- define "confidential-router-api.consoleUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "consoleHostname must be set" .Values.consoleHostname) -}}
{{- end -}}

{{/* The bundled database's service, unless an external host was given. */}}
{{- define "confidential-router-api.databaseHost" -}}
{{- if .Values.database.host -}}
{{- .Values.database.host -}}
{{- else if .Values.postgresql.enabled -}}
{{- .Values.postgresql.fullnameOverride -}}
{{- else -}}
{{- fail "database.host must be set when postgresql.enabled is false" -}}
{{- end -}}
{{- end -}}

{{/* The secret this chart creates. Never the one a value points at. */}}
{{- define "confidential-router-api.secretName" -}}
{{- include "confidential-router-api.fullname" . -}}
{{- end -}}

{{/*
The catalogue keys that are switched on, as a JSON array so the caller can
`fromJsonArray` it. A boolean naming an entry the catalogue does not have is a
typo, and a typo that silently served nothing would be found in production.
*/}}
{{- define "confidential-router-api.enabledModelKeys" -}}
{{- $keys := list -}}
{{- range $key, $on := .Values.models -}}
{{- if $on -}}
{{- if not (hasKey $.Values.modelCatalog $key) -}}
{{- fail (printf "models.%s is on, but modelCatalog has no entry named %s" $key $key) -}}
{{- end -}}
{{- $keys = append $keys $key -}}
{{- end -}}
{{- end -}}
{{- toJson $keys -}}
{{- end -}}

{{/* The endpoint names the enabled models refer to, deduplicated, sorted. */}}
{{- define "confidential-router-api.referencedEndpoints" -}}
{{- $seen := dict -}}
{{- range $key := (include "confidential-router-api.enabledModelKeys" . | fromJsonArray) -}}
{{- $model := get $.Values.modelCatalog $key -}}
{{- $name := required (printf "modelCatalog.%s.endpoint must name an endpoint" $key) $model.endpoint -}}
{{- if not (hasKey $.Values.endpoints $name) -}}
{{- fail (printf "modelCatalog.%s.endpoint is %q, which endpoints does not define" $key $name) -}}
{{- end -}}
{{- $_ := set $seen $name true -}}
{{- end -}}
{{- keys $seen | sortAlpha | toJson -}}
{{- end -}}

{{/*
Configuration checks that are cheaper to fail here than in a crash loop.
*/}}
{{- define "confidential-router-api.validate" -}}
{{- if and .Values.database.existingSecret .Values.postgresql.enabled -}}
{{- fail "database.existingSecret points at an external database, but postgresql.enabled is true: set postgresql.enabled to false" -}}
{{- end -}}
{{- if and .Values.database.url .Values.postgresql.enabled -}}
{{- fail "database.url points at an external database, but postgresql.enabled is true: set postgresql.enabled to false" -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (not .Values.database.password) -}}
{{- fail "database.password must be set: it is both what the bundled PostgreSQL is created with and what the API connects with" -}}
{{- end -}}
{{- if not (or .Values.postgresql.enabled .Values.database.url .Values.database.password .Values.database.existingSecret) -}}
{{- fail "postgresql.enabled is false: set database.url, or database.password, or database.existingSecret" -}}
{{- end -}}
{{- if not (or .Values.auth.secret .Values.auth.existingSecret) -}}
{{- fail "auth.secret must be at least 32 characters, or auth.existingSecret must name a secret holding one. It signs session cookies and magic-link tokens" -}}
{{- end -}}
{{- if and .Values.auth.secret (lt (len .Values.auth.secret) 32) -}}
{{- fail "auth.secret must be at least 32 characters" -}}
{{- end -}}
{{- if and .Values.postgresql.enabled (ne .Values.postgresql.auth.existingSecret (include "confidential-router-api.fullname" .)) -}}
{{- fail (printf "postgresql.auth.existingSecret must be %q — the secret this chart creates — or the server and the API will disagree on the password" (include "confidential-router-api.fullname" .)) -}}
{{- end -}}
{{- if not (has .Values.billing.mode (list "manual" "stripe")) -}}
{{- fail (printf "billing.mode must be manual or stripe, not %q" .Values.billing.mode) -}}
{{- end -}}
{{- if eq .Values.billing.mode "stripe" -}}
{{- if not (or .Values.billing.stripe.existingSecret (and .Values.billing.stripe.secretKey .Values.billing.stripe.webhookSecret)) -}}
{{- fail "billing.mode is stripe: set billing.stripe.secretKey and billing.stripe.webhookSecret, or billing.stripe.existingSecret" -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.auth.magicLink.mailer "smtp" -}}
{{- fail "auth.magicLink.mailer \"smtp\" is in the config schema but not implemented: use \"resend\", or \"console\" outside production" -}}
{{- end -}}
{{- if not (has .Values.auth.magicLink.mailer (list "console" "resend")) -}}
{{- fail (printf "auth.magicLink.mailer must be console or resend, not %q" .Values.auth.magicLink.mailer) -}}
{{- end -}}
{{- if eq (include "confidential-router-api.nodeEnv" .) "production" -}}
{{- if eq .Values.auth.magicLink.mailer "console" -}}
{{- fail "auth.magicLink.mailer is console under NODE_ENV=production: sign-in links would be written to the log instead of sent. Configure the resend mailer" -}}
{{- end -}}
{{- if ne .Values.billing.mode "stripe" -}}
{{- fail "nodeEnv is production but billing.mode is not stripe: the manual payment provider mints credit from a signed link and the API refuses to bind it in production" -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.auth.magicLink.mailer "resend" -}}
{{- if not (or .Values.auth.magicLink.resendApiKey .Values.auth.magicLink.resendApiKeyExistingSecret) -}}
{{- fail "auth.magicLink.mailer is resend: set auth.magicLink.resendApiKey or auth.magicLink.resendApiKeyExistingSecret" -}}
{{- end -}}
{{- end -}}
{{- end -}}
