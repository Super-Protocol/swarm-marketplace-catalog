{{/*
Names, fixed rather than release-derived: the litellm chart finds this server by
DNS and its `ollamaUrl` default names this service.
*/}}
{{- define "confidential-router-ollama.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-ollama.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-ollama.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "confidential-router-ollama.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: confidential-router
{{- end -}}

{{- define "confidential-router-ollama.selectorLabels" -}}
app.kubernetes.io/name: {{ include "confidential-router-ollama.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "confidential-router-ollama.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{/*
The selection, checked. Each name is written into a shell command, so it has to
be an Ollama model reference and nothing that a shell would read as more than
one word; a name given twice would be pulled twice and served once.
*/}}
{{- define "confidential-router-ollama.models" -}}
{{- $seen := dict -}}
{{- range $name := .Values.models -}}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9._-]+)?$" $name) -}}
{{- fail (printf "models: %q is not an Ollama model name" $name) -}}
{{- end -}}
{{- if hasKey $seen $name -}}
{{- fail (printf "models lists %q twice" $name) -}}
{{- end -}}
{{- $_ := set $seen $name true -}}
{{- end -}}
{{- toJson .Values.models -}}
{{- end -}}
