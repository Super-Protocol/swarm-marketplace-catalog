{{/*
Object names derive from the release, so two deployments of this listing in one cluster never
collide and nothing inside the chart has to guess what the other half is called.
*/}}
{{- define "cp.fullname" -}}
{{- .Release.Name | trunc 45 | trimSuffix "-" -}}
{{- end -}}

{{- define "cp.kafka.name" -}}
{{ include "cp.fullname" . }}-kafka
{{- end -}}

{{- define "cp.console.name" -}}
{{ include "cp.fullname" . }}-control-center
{{- end -}}

{{- define "cp.schemaRegistry.name" -}}
{{ include "cp.fullname" . }}-schema-registry
{{- end -}}

{{/*
The Kafka cluster id: a base64 UUID, which is 22 characters from the base64 alphabet decoding to
16 bytes. A hex digest satisfies that alphabet, and deriving it from namespace and release makes it
both unique per deployment and identical on every re-render — which matters because the log
directory is formatted with it and will not open under another.
*/}}
{{- define "cp.clusterId" -}}
{{- if .Values.kafka.clusterId -}}
{{ .Values.kafka.clusterId }}
{{- else -}}
{{ printf "%s/%s" .Release.Namespace .Release.Name | sha256sum | trunc 22 }}
{{- end -}}
{{- end -}}

{{/* Where clients inside the namespace reach the broker. */}}
{{- define "cp.bootstrap" -}}
{{ include "cp.kafka.name" . }}:9092
{{- end -}}
