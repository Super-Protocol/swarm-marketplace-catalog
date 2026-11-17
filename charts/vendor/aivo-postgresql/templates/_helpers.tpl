{{/* Полное имя релиза: имя релиза как есть, чтобы адрес БД совпадал с тем, что
     инженер пишет в values платформы (host: aivo-postgresql). */}}
{{- define "postgresql.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "postgresql.labels" -}}
app.kubernetes.io/name: postgresql
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "postgresql.selectorLabels" -}}
app.kubernetes.io/name: postgresql
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Имя секрета с паролями: свой или переданный заказчиком. */}}
{{- define "postgresql.secretName" -}}
{{- if .Values.auth.existingSecret -}}
{{ .Values.auth.existingSecret }}
{{- else -}}
{{ include "postgresql.fullname" . }}
{{- end -}}
{{- end -}}
