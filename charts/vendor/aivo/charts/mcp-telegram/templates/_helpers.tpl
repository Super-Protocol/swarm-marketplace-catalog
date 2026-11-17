{{/*
Helper names are prefixed with the chart name rather than the `app.` the other
subcharts share: named templates are global across an umbrella release, and the
two connector charts are near-copies of each other — a shared prefix would let
one silently render with the other's definitions.
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "mcp-telegram.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified name: `<release>-mcp-telegram`. This is also the Service name the
api reaches the connector by — `http://<release>-mcp-telegram:8931/mcp` in
`config.services.api.mcp.globalServers[].url` — so renaming it breaks that URL.
*/}}
{{- define "mcp-telegram.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "mcp-telegram.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "mcp-telegram.labels" -}}
helm.sh/chart: {{ include "mcp-telegram.chart" . }}
{{ include "mcp-telegram.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mcp-telegram.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mcp-telegram.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
