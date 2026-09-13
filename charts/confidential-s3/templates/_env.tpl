{{/*
The database DSN, assembled by Kubernetes from an earlier variable in the same
list. That is what keeps the password in a Secret while the rest of the DSN stays
legible in the manifest — and what keeps it out of the evidence snapshot, which
is taken over the manifests with every Secret already lifted out.

A password with characters that would need percent-encoding has to come through
`database.url` instead; the pieces do not encode for you.
*/}}
{{- define "confidential-s3.databaseEnv" -}}
{{- if .Values.database.url }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "confidential-s3.secretName" . }}
      key: database-url
{{- else }}
- name: CS3_DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret | default (include "confidential-s3.secretName" .) }}
      key: {{ if .Values.database.existingSecret }}{{ .Values.database.existingSecretKey }}{{ else }}password{{ end }}
- name: DATABASE_URL
  value: {{ printf "postgres://%s:$(CS3_DATABASE_PASSWORD)@%s:%v/%s?sslmode=%s" .Values.database.user (include "confidential-s3.databaseHost" .) .Values.database.port .Values.database.name .Values.database.sslmode | quote }}
{{- end }}
{{- end -}}

{{/*
The 32-byte application key, on both sides of the deployment.

The control plane seals a registered back storage's credentials and wraps every
encrypted bucket's KEK with it; the gateway opens them with the same key. Two
halves that disagree is a deployment that stores data it cannot read back, which
is why both read it from one Secret key rather than from two values.
*/}}
{{- define "confidential-s3.masterKeyEnv" -}}
- name: MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.masterKey.existingSecret | default (include "confidential-s3.secretName" .) }}
      key: {{ if .Values.masterKey.existingSecret }}{{ .Values.masterKey.existingSecretKey }}{{ else }}master-key{{ end }}
{{- end -}}

{{- define "confidential-s3.engineCredentialEnv" -}}
- name: INTERNAL_ENGINE_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.engine.existingSecret | default (include "confidential-s3.secretName" .) }}
      key: engine-access-key
- name: INTERNAL_ENGINE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.engine.existingSecret | default (include "confidential-s3.secretName" .) }}
      key: engine-secret-key
{{- end -}}
