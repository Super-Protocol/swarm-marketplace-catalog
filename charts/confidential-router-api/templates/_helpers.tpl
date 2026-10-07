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
The egress sidecar's image reference. Same rule as the router's own: a digest
wins over a tag, and one of the two has to be there, because this container is
inside the snapshot a user pins (ADR-008 §2).
*/}}
{{- define "confidential-router-api.sidecarImage" -}}
{{- $image := .Values.externalEndpoints.image -}}
{{- $repo := printf "%s/%s" $image.registry $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repo $image.digest -}}
{{- else if $image.tag -}}
{{- printf "%s:%s" $repo $image.tag -}}
{{- else -}}
{{- fail "externalEndpoints.image.digest is empty and externalEndpoints.image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{/*
The directory the shared `emptyDir` is mounted at, in both containers.

Derived from the one path rather than configured beside it: router-api writes
that file and the sidecar watches it, and a chart that let the two be set
separately would have a setting whose only effect is an egress that never
reloads.
*/}}
{{- define "confidential-router-api.sidecarConfigDir" -}}
{{- dir (required "externalEndpoints.configPath must be set" .Values.externalEndpoints.configPath) -}}
{{- end -}}

{{/*
NODE_ENV — `production`, and nothing this chart is given changes that.

It used to be derived: `development` unless billing ran on Stripe, because the
API refuses to bind the credit-minting manual provider in production. A
deployment of the router therefore published `NODE_ENV: development`, which is
how that provider ended up live on a public hostname handing every account holder
an unbounded free-credit button (SUP-167). The chart only ever deploys to a
cluster, so production is the only honest value and `billing.mode: manual` is
refused instead.

`nodeEnv` is still overridable, for the one combination that needs the console
mailer — but it no longer decides whether credit can be minted: the API checks
`server.publicBaseUrl` for that, and this chart never renders a loopback one.
*/}}
{{- define "confidential-router-api.nodeEnv" -}}
{{- .Values.nodeEnv | default "production" -}}
{{- end -}}

{{- define "confidential-router-api.apiUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "apiHostname must be set" .Values.apiHostname) -}}
{{- end -}}

{{- define "confidential-router-api.consoleUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "consoleHostname must be set" .Values.consoleHostname) -}}
{{- end -}}

{{- define "confidential-router-api.landingUrl" -}}
{{- printf "%s://%s" .Values.publicScheme .Values.invites.landingHostname -}}
{{- end -}}

{{/*
Every origin that calls this API from a browser, as a JSON array: the console
always, and the campaign landing page when there is one. It is both the CORS
allow-list and Better Auth's trusted origins, so an origin missing from it is a
page whose fetch the browser throws away — and one listed twice is no error, only
noise in a file operators read, which is why the same origin twice collapses to
one entry.
*/}}
{{- define "confidential-router-api.clientOrigins" -}}
{{- $origins := list (include "confidential-router-api.consoleUrl" .) -}}
{{- if .Values.invites.landingHostname -}}
{{- $origins = append $origins (include "confidential-router-api.landingUrl" .) -}}
{{- end -}}
{{- $origins | uniq | toJson -}}
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
The ConfigMap carrying the hostname-derived values `router.yaml` refers to by
placeholder. Separate from the attested one so the exclusion that stabilises this
listing's evidence digest can name a whole object rather than a field inside a
document (SUP-211); see the comment at the top of `configmap-public.yaml`.
*/}}
{{- define "confidential-router-api.publicConfigName" -}}
{{- printf "%s-public" (include "confidential-router-api.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The selected model names, as a JSON array so the caller can `fromJsonArray` it.

The list is passed through in the order it was given — that is the order the
console shows — but a name the catalogue does not know, or one given twice, is a
values mistake that would otherwise render a catalogue nobody asked for.
*/}}
{{- define "confidential-router-api.selectedModels" -}}
{{- $known := keys .Values.modelCatalog | sortAlpha -}}
{{- $seen := dict -}}
{{- range $name := .Values.models -}}
{{- if not (hasKey $.Values.modelCatalog $name) -}}
{{- fail (printf "models lists %q, which modelCatalog has no entry for. Known: %s" $name (join ", " $known)) -}}
{{- end -}}
{{- if hasKey $seen $name -}}
{{- fail (printf "models lists %q twice" $name) -}}
{{- end -}}
{{- $_ := set $seen $name true -}}
{{- end -}}
{{- toJson .Values.models -}}
{{- end -}}

{{/* The endpoint names the selected models refer to, deduplicated, sorted. */}}
{{- define "confidential-router-api.referencedEndpoints" -}}
{{- $seen := dict -}}
{{- range $name := (include "confidential-router-api.selectedModels" . | fromJsonArray) -}}
{{- $model := get $.Values.modelCatalog $name -}}
{{- $endpoint := required (printf "modelCatalog.%q.endpoint must name an endpoint" $name) $model.endpoint -}}
{{- if not (hasKey $.Values.endpoints $endpoint) -}}
{{- fail (printf "modelCatalog.%q.endpoint is %q, which endpoints does not define" $name $endpoint) -}}
{{- end -}}
{{- $_ := set $seen $endpoint true -}}
{{- end -}}
{{- keys $seen | sortAlpha | toJson -}}
{{- end -}}

{{/*
Does any attested endpoint fall back to this deployment's own API hostname?

Non-empty when at least one referenced endpoint named no hostname of its own, which is
what makes `${ROUTER_PUBLIC_HOSTNAME}` appear in the rendered config. The public
ConfigMap renders that key only then: a key nothing refers to would be a field excluded
from the evidence snapshot for no reason, and `charts/tests/config_placeholders.py`
fails on it. A deployment with every model switched off has no endpoints at all.
*/}}
{{- define "confidential-router-api.endpointsUsePublicHostname" -}}
{{- range $name := (include "confidential-router-api.referencedEndpoints" . | fromJsonArray) -}}
{{- if not (get $.Values.endpoints $name).hostname -}}
{{- "yes" -}}
{{- end -}}
{{- end -}}
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
{{/*
The bundled database has to accept a connection in the clear, because that is the only
kind the API makes.

`database.sslmode` reaches the DSN and goes no further: TypeORM parses the URL with a
regex of its own and discards the query string, so the driver is never told to
negotiate TLS no matter what is in there. The database chart's default `pg_hba.conf`
ends in `hostnossl all all all reject`, and the two together deploy cleanly and
present as an API that cannot reach a database every probe says is healthy.

The fix is on the router's side — TypeORM's own `ssl` option, which the rendered
config does not set — so this refuses rather than papering over it.
*/}}
{{- if and .Values.postgresql.enabled .Values.postgresql.requireSsl -}}
{{- fail "postgresql.requireSsl is true, and the API connects in the clear: it builds a DSN with ?sslmode=, which TypeORM discards along with the rest of the query string. The bundled server would refuse every connection. Set postgresql.requireSsl: false, or give the router a TypeORM ssl option first" -}}
{{- end -}}
{{- if not (has .Values.billing.mode (list "disabled" "manual" "stripe")) -}}
{{- fail (printf "billing.mode must be disabled or stripe, not %q" .Values.billing.mode) -}}
{{- end -}}
{{- if eq .Values.billing.mode "stripe" -}}
{{- if not (or .Values.billing.stripe.existingSecret (and .Values.billing.stripe.secretKey .Values.billing.stripe.webhookSecret)) -}}
{{- fail "billing.mode is stripe: set billing.stripe.secretKey and billing.stripe.webhookSecret, or billing.stripe.existingSecret" -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.auth.magicLink.mailer "smtp" -}}
{{- fail "auth.magicLink.mailer \"smtp\" is in the config schema but not implemented: use \"resend\", or \"console\" outside production" -}}
{{- end -}}
{{- if not (has .Values.auth.magicLink.mailer (list "none" "console" "resend")) -}}
{{- fail (printf "auth.magicLink.mailer must be none, console or resend, not %q" .Values.auth.magicLink.mailer) -}}
{{- end -}}
{{- if .Values.auth.password.enabled -}}
{{- $minLength := .Values.auth.password.minLength | int -}}
{{- if or (lt $minLength 8) (gt $minLength 128) -}}
{{- fail (printf "auth.password.minLength must be between 8 and 128, not %d" $minLength) -}}
{{- end -}}
{{- end -}}
{{/*
Every sign-in path off at once deploys cleanly and answers nothing but 404: the
bootstrap token lets exactly one account in and then stops existing, so a
deployment that has no mailer, no OAuth app and no password has no second way in
even for the administrator who claimed it.
*/}}
{{- if not (or .Values.auth.password.enabled .Values.auth.github.clientId .Values.auth.google.clientId (ne .Values.auth.magicLink.mailer "none")) -}}
{{- fail "no sign-in path is configured: auth.magicLink.mailer is none, auth.password.enabled is false and neither auth.github nor auth.google is set. The bootstrap token creates one account and then stops existing, so nobody could sign in afterwards" -}}
{{- end -}}
{{/*
The manual provider mints credit from a signed link. It exists for a laptop, and
the API refuses to bind it in production or on a publicBaseUrl anyone else can
reach — which is every hostname this chart renders. A deployment that sells
nothing is `mode: disabled`, which refuses checkout instead of minting.
*/}}
{{- if eq .Values.billing.mode "manual" -}}
{{- fail "billing.mode is manual: that provider mints credit from a signed link and the API refuses to bind it on a public hostname. Use disabled to sell nothing, or stripe to take payments" -}}
{{- end -}}
{{- if eq (include "confidential-router-api.nodeEnv" .) "production" -}}
{{- if eq .Values.auth.magicLink.mailer "console" -}}
{{- fail "auth.magicLink.mailer is console under NODE_ENV=production: sign-in links would be written to the log instead of sent. Configure the resend mailer, or set nodeEnv: development if reading them out of the pod log is the deliberate choice" -}}
{{- end -}}
{{- end -}}
{{- if and (eq .Values.billing.mode "stripe") (ne (include "confidential-router-api.nodeEnv" .) "production") -}}
{{- fail "billing.mode is stripe but nodeEnv is not production: real card payments must not run in a mode that relaxes the checks around them" -}}
{{- end -}}
{{- if eq .Values.auth.magicLink.mailer "resend" -}}
{{- if not (or .Values.auth.magicLink.resendApiKey .Values.auth.magicLink.resendApiKeyExistingSecret) -}}
{{- fail "auth.magicLink.mailer is resend: set auth.magicLink.resendApiKey or auth.magicLink.resendApiKeyExistingSecret" -}}
{{- end -}}
{{- end -}}
{{/*
A hostname, like the two above it. Given a URL it would render
`https://https://router.example` into the CORS list, which no browser origin ever
matches — and the symptom is a page that loads and quietly does nothing.
*/}}
{{- if .Values.invites.landingHostname -}}
{{- if contains "/" .Values.invites.landingHostname -}}
{{- fail (printf "invites.landingHostname must be a hostname, not a URL: %q" .Values.invites.landingHostname) -}}
{{- end -}}
{{- end -}}
{{/*
The attested egress. Both of these render cleanly and then do not work: a
deployment whose administrator can register an upstream but never store its API
key, and a second container pulled by a tag that moved.
*/}}
{{- if .Values.externalEndpoints.enabled -}}
{{- if not (or .Values.externalEndpoints.secretsKey .Values.externalEndpoints.existingSecret) -}}
{{- fail "externalEndpoints.enabled is true: set externalEndpoints.secretsKey, or externalEndpoints.existingSecret. It is CR_API_SECRETS_KEY, the AES-256 key an upstream API key is sealed under, and without it every registration is refused" -}}
{{- end -}}
{{- if .Values.externalEndpoints.secretsKey -}}
{{- $length := len .Values.externalEndpoints.secretsKey -}}
{{- if not (has $length (list 43 44 64)) -}}
{{- fail (printf "externalEndpoints.secretsKey is %d characters: it has to decode to exactly 32 bytes, which is 43 characters of unpadded base64url, 44 of base64, or 64 of hex" $length) -}}
{{- end -}}
{{- end -}}
{{- $port := .Values.externalEndpoints.adminPort | int -}}
{{- if or (lt $port 1024) (gt $port 65535) -}}
{{- fail (printf "externalEndpoints.adminPort must be between 1024 and 65535, not %d" $port) -}}
{{- end -}}
{{- if eq $port (.Values.service.targetPort | int) -}}
{{- fail (printf "externalEndpoints.adminPort is %d, which is the port the API itself listens on: the two containers share one network namespace, so one of them would fail to bind" $port) -}}
{{- end -}}
{{- end -}}
{{/*
The addresses travel to the pod as one comma-separated variable, so a comma
inside one of them would split it into two addresses that match nobody. An entry
without an `@` is a name where an address was meant, which the guard compares
against a signed-in email and always refuses.
*/}}
{{- range $email := .Values.auth.adminEmails -}}
{{- if not (contains "@" $email) -}}
{{- fail (printf "auth.adminEmails contains %q, which is not an email address" $email) -}}
{{- end -}}
{{- if contains "," $email -}}
{{- fail (printf "auth.adminEmails contains %q: one address per list entry, commas separate them" $email) -}}
{{- end -}}
{{- end -}}
{{/*
The same question about the one address the first account is created under. A
name where an address was meant is a `POST /auth/bootstrap` that creates an
account nobody can sign in to, and the token is spent on it.
*/}}
{{- if .Values.auth.bootstrapEmail -}}
{{- if not (contains "@" .Values.auth.bootstrapEmail) -}}
{{- fail (printf "auth.bootstrapEmail is %q, which is not an email address" .Values.auth.bootstrapEmail) -}}
{{- end -}}
{{- end -}}
{{- end -}}
