{{- define "confidential-router-ui.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-ui.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-ui.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "confidential-router-ui.labels" -}}
helm.sh/chart: {{ include "confidential-router-ui.chart" . }}
{{ include "confidential-router-ui.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: confidential-router
{{- end -}}

{{- define "confidential-router-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "confidential-router-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "confidential-router-ui.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{- define "confidential-router-ui.apiOrigin" -}}
{{- printf "%s://%s" .Values.publicScheme (required "apiHostname must be set" .Values.apiHostname) -}}
{{- end -}}

{{- define "confidential-router-ui.consoleUrl" -}}
{{- printf "%s://%s" .Values.publicScheme (required "consoleHostname must be set" .Values.consoleHostname) -}}
{{- end -}}
