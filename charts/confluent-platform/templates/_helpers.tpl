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
The Kafka cluster id: 22 characters from the base64 alphabet, decoding to the 16 bytes Kafka wants.
Constant rather than derived — see the note in values.yaml: it is what keeps two deployments of this
listing byte-identical, and therefore attestable against one published digest.
*/}}
{{- define "cp.clusterId" -}}
{{ required "kafka.clusterId is required" .Values.kafka.clusterId }}
{{- end -}}

{{/* Where clients inside the namespace reach the broker. */}}
{{- define "cp.bootstrap" -}}
{{ include "cp.kafka.name" . }}:9092
{{- end -}}

{{/* The admin credential, in the one-line form every platform component takes it. */}}
{{- define "cp.adminJaasRef" -}}
valueFrom:
  secretKeyRef:
    name: {{ include "cp.fullname" . }}-sasl
    key: admin.sasl.jaas.config
{{- end -}}
