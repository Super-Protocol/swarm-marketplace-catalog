{{/*
Fails the render rather than silently deploying services with an empty password.
Helm's `required` is the only place this can be caught before manifests exist.
*/}}
{{- define "platform.internalSecret" -}}
{{- required "internalSecret must be set: it is the password for every backing service" .Values.internalSecret -}}
{{- end -}}
