{{- define "feed.fullname" -}}
{{ .Release.Name | trunc 45 | trimSuffix "-" }}
{{- end -}}
