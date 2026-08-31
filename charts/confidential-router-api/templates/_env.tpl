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

{{- define "confidential-router-api.volumeMounts" -}}
- name: config
  mountPath: /etc/confidential-router
  readOnly: true
- name: tmp
  mountPath: /tmp
- name: data
  mountPath: /app/data
{{- end -}}
