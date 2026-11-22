{{- define "rapids-singlecell-notebook.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fixed rather than release-derived, like the other charts in this repository: the
object names are what a NOTES line, an ingress rule and a listing's outputs all
have to agree on, and a release name chosen by the marketplace is not something
a listing can quote.
*/}}
{{- define "rapids-singlecell-notebook.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rapids-singlecell-notebook.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rapids-singlecell-notebook.labels" -}}
helm.sh/chart: {{ include "rapids-singlecell-notebook.chart" . }}
{{ include "rapids-singlecell-notebook.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: notebook
{{- end -}}

{{- define "rapids-singlecell-notebook.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rapids-singlecell-notebook.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "rapids-singlecell-notebook.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{- define "rapids-singlecell-notebook.secretName" -}}
{{- .Values.existingSecret | default (include "rapids-singlecell-notebook.fullname" .) -}}
{{- end -}}

{{- define "rapids-singlecell-notebook.secretKey" -}}
{{- if .Values.existingSecret -}}{{ .Values.existingSecretKey }}{{- else -}}token{{- end -}}
{{- end -}}

{{/*
Each of these is a deployment that renders cleanly and then fails in the
cluster, or serves a GPU and somebody's patient data to the public. Refused
while there is still a person looking at it.
*/}}
{{- define "rapids-singlecell-notebook.validate" -}}
{{- if and .Values.ingress.enabled (not .Values.hostname) -}}
{{- fail "hostname must be set when the ingress is enabled: an Ingress with no host answers for every name that reaches the controller" -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not (or .Values.token .Values.existingSecret)) -}}
{{- fail "token must be set (or existingSecret named) before this can be published: the image's own command starts JupyterLab with --NotebookApp.token='', which is a GPU and whatever data is bound to it handed to whoever finds the hostname" -}}
{{- end -}}
{{- if and .Values.gpu.enabled (not .Values.gpu.runtimeClassName) -}}
{{- fail "gpu.runtimeClassName must be set when gpu.enabled is true: a device request without the runtime class leaves the pod Pending for ever rather than falling back to CPU" -}}
{{- end -}}
{{- if and .Values.gpu.enabled (lt (int .Values.gpu.count) 1) -}}
{{- fail "gpu.count must be at least 1 when gpu.enabled is true" -}}
{{- end -}}
{{- if not .Values.persistence.enabled -}}
{{- fail "persistence.enabled must be true: the workspace holds the tenant's notebooks and their derived matrices, and an emptyDir loses all of it when the pod moves" -}}
{{- end -}}
{{- end -}}
