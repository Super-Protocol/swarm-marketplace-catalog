{{/*
The process environment shared by the server and the migration init container.

Only secrets and the config-file path live here. Everything else is in the
rendered `router.yaml`, so there is one place to read a deployment's settings
rather than two that can disagree.

`ROUTER_DATABASE_URL` is assembled by Kubernetes from an earlier variable in the
same list — that is what keeps the password in a Secret while the rest of the DSN
stays legible in the manifest. A password with characters that need
percent-encoding has to come through `database.url` instead.
*/}}
{{- define "confidential-router-api.env" -}}
- name: NODE_ENV
  value: {{ include "confidential-router-api.nodeEnv" . | quote }}
- name: CR_API_CONFIG_FILE
  value: /etc/confidential-router/router.yaml
{{- if .Values.database.url }}
- name: ROUTER_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "confidential-router-api.secretName" . }}
      key: database-url
{{- else }}
- name: ROUTER_DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.database.existingSecret }}{{ .Values.database.existingSecretKey }}{{ else }}password{{ end }}
- name: ROUTER_DATABASE_URL
  value: {{ printf "postgres://%s:$(ROUTER_DATABASE_PASSWORD)@%s:%v/%s?sslmode=%s" .Values.database.user (include "confidential-router-api.databaseHost" .) .Values.database.port .Values.database.name .Values.database.sslmode | quote }}
{{- end }}
- name: ROUTER_AUTH_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.auth.existingSecret }}{{ .Values.auth.existingSecretKey }}{{ else }}auth-secret{{ end }}
{{- if or .Values.litellm.apiKey .Values.litellm.existingSecret }}
- name: ROUTER_LITELLM_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.litellm.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.litellm.existingSecret }}{{ .Values.litellm.existingSecretKey }}{{ else }}litellm-api-key{{ end }}
{{- end }}
{{- if eq .Values.billing.mode "stripe" }}
- name: ROUTER_STRIPE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.billing.stripe.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.billing.stripe.existingSecret }}{{ .Values.billing.stripe.secretKeyKey }}{{ else }}stripe-secret-key{{ end }}
- name: ROUTER_STRIPE_WEBHOOK_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.billing.stripe.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.billing.stripe.existingSecret }}{{ .Values.billing.stripe.webhookSecretKey }}{{ else }}stripe-webhook-secret{{ end }}
{{- end }}
{{- if eq .Values.auth.magicLink.mailer "resend" }}
- name: ROUTER_RESEND_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.magicLink.resendApiKeyExistingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.auth.magicLink.resendApiKeyExistingSecret }}{{ .Values.auth.magicLink.resendApiKeyExistingSecretKey }}{{ else }}resend-api-key{{ end }}
{{- end }}
{{- if or .Values.auth.bootstrapToken .Values.auth.bootstrapTokenExistingSecret }}
{{- /*
The one `CR_API_*` variable here, and deliberately so: this is configuration, not
a placeholder the config file substitutes. `CR_API_AUTH__BOOTSTRAP_TOKEN` is read
by the environment layer as `auth.bootstrapToken`, which is a key the schema has
only from SUP-95 onwards — hence the condition around it.
*/}}
- name: CR_API_AUTH__BOOTSTRAP_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.bootstrapTokenExistingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.auth.bootstrapTokenExistingSecret }}{{ .Values.auth.bootstrapTokenExistingSecretKey }}{{ else }}bootstrap-token{{ end }}
{{- if or .Values.auth.bootstrapEmail .Values.auth.bootstrapEmailExistingSecret }}
{{- /*
Out of the Secret and not a plain `value:`, for the same reason
`CR_API_AUTH__ADMIN_EMAILS` below is: this is the address of whoever deployed
this, and a literal here is published. The container's env list is part of the
attested snapshot, so the address would be readable by anyone who fetches
`/.well-known/swarm-evidence` — and the digest would be a property of who
deployed it rather than of the version, which is the one thing it must not be
(SUP-241). It is the same value as the admin list, and one copy of it being
protected while the other was published was an oversight, not a decision.
*/}}
- name: CR_API_AUTH__BOOTSTRAP_EMAIL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.bootstrapEmailExistingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.auth.bootstrapEmailExistingSecret }}{{ .Values.auth.bootstrapEmailExistingSecretKey }}{{ else }}bootstrap-email{{ end }}
{{- end }}
{{- end }}
{{- if or .Values.auth.adminEmails .Values.auth.adminEmailsExistingSecret }}
{{- /*
`CR_API_AUTH__ADMIN_EMAILS` — the environment layer reads it as
`auth.adminEmails`, and the router's schema splits a comma-separated value into
the list. A `CR_API_*` variable rather than a `${…}` placeholder in the config
file for the same reason as the bootstrap token: the value is configuration that
has to stay out of an attested, published ConfigMap, not a credential the file
refers to.
*/}}
- name: CR_API_AUTH__ADMIN_EMAILS
  valueFrom:
    secretKeyRef:
      name: {{ .Values.auth.adminEmailsExistingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.auth.adminEmailsExistingSecret }}{{ .Values.auth.adminEmailsExistingSecretKey }}{{ else }}admin-emails{{ end }}
{{- end }}
{{- if or .Values.analytics.posthog.projectKey .Values.analytics.posthog.existingSecret }}
{{- /*
`POSTHOG_PROJECT_KEY` and `POSTHOG_HOST`, not `CR_API_ANALYTICS__*`: the two names
were fixed for every surface of this product before any of them was written
(SUP-143), and the router maps them into `analytics.posthog` as its
lowest-precedence layer. Being outside the prefix is also what makes them safe on
an older image: an unknown `CR_API_*` variable fails a strict schema, while these
two are simply not read.
*/}}
- name: POSTHOG_PROJECT_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.analytics.posthog.existingSecret | default (include "confidential-router-api.secretName" .) }}
      key: {{ if .Values.analytics.posthog.existingSecret }}{{ .Values.analytics.posthog.existingSecretKey }}{{ else }}posthog-project-key{{ end }}
{{- if .Values.analytics.posthog.host }}
- name: POSTHOG_HOST
  value: {{ .Values.analytics.posthog.host | quote }}
{{- end }}
{{- end }}
{{- if .Values.auth.github.clientId }}
- name: ROUTER_GITHUB_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "confidential-router-api.secretName" . }}
      key: github-client-secret
{{- end }}
{{- if .Values.auth.google.clientId }}
- name: ROUTER_GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "confidential-router-api.secretName" . }}
      key: google-client-secret
{{- end }}
{{- end -}}

{{/*
The hostname-derived values `router.yaml` names by placeholder.

`envFrom` rather than five `env` entries: this is one object the evidence
snapshot excludes whole, instead of five index-based pointers into a container's
env list that would silently point at the wrong value the next time a variable is
added above them.

Both the server and the migration container need it. The init container loads the
same config file, and a placeholder with no value fails the boot — so a migration
container without this would make every deployment a crash loop before the server
ever started.
*/}}
{{- define "confidential-router-api.envFrom" -}}
- configMapRef:
    name: {{ include "confidential-router-api.publicConfigName" . }}
{{- end -}}

{{- define "confidential-router-api.volumeMounts" -}}
- name: config
  mountPath: /etc/confidential-router
  readOnly: true
- name: tmp
  mountPath: /tmp
- name: data
  mountPath: /app/data
{{- end -}}
