{{/*
Names, fixed rather than release-derived: the router API finds this proxy by DNS
and its `litellmUrl` default names this service.
*/}}
{{- define "confidential-router-litellm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-litellm.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-litellm.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-litellm.labels" -}}
helm.sh/chart: {{ include "confidential-router-litellm.chart" . }}
{{ include "confidential-router-litellm.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: confidential-router
{{- end -}}

{{- define "confidential-router-litellm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "confidential-router-litellm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "confidential-router-litellm.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{/* The catalogue keys that are switched on, as a JSON array. */}}
{{- define "confidential-router-litellm.enabledModelKeys" -}}
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
