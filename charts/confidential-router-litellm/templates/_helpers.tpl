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

{{/*
The selected model names, as a JSON array. The list is passed through in the
order it was given; a name given twice would render two `model_list` entries
LiteLLM would resolve arbitrarily between.
*/}}
{{- define "confidential-router-litellm.selectedModels" -}}
{{- $seen := dict -}}
{{- range $name := .Values.models -}}
{{- if hasKey $seen $name -}}
{{- fail (printf "models lists %q twice" $name) -}}
{{- end -}}
{{- $_ := set $seen $name true -}}
{{- end -}}
{{- toJson .Values.models -}}
{{- end -}}
