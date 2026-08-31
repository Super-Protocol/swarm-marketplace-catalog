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

{{/*
The one check worth failing a render over. A console image built for another
origin deploys perfectly, serves every page, and cannot sign anyone in: the
browser bundle calls the origin it was compiled with, which in the published
default is localhost.
*/}}
{{- define "confidential-router-ui.validate" -}}
{{- $origin := include "confidential-router-ui.apiOrigin" . -}}
{{- if and .Values.image.builtForApiOrigin (ne .Values.image.builtForApiOrigin $origin) -}}
{{- fail (printf "this console image was built for %s but is being deployed against %s. next build inlines NEXT_PUBLIC_* into the client bundle, so the browser would keep calling %s. Rebuild router-ui with NEXT_PUBLIC_API_ORIGIN=%s, publish it, and set image.digest and image.builtForApiOrigin together — or empty image.builtForApiOrigin to skip this check" .Values.image.builtForApiOrigin $origin .Values.image.builtForApiOrigin $origin) -}}
{{- end -}}
{{- end -}}
