{{- define "milvus.name" -}}
{{- required "milvus-standalone: name is required — it is the Service name another component has to write down, so it cannot be release-derived" .Values.name -}}
{{- end -}}

{{- define "milvus.labels" -}}
app.kubernetes.io/name: {{ include "milvus.name" . }}
app.kubernetes.io/component: vector-store
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "milvus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "milvus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "milvus.imageRef" -}}
{{- $repo := printf "%s/%s" .registry .repository -}}
{{- if .digest -}}
{{- printf "%s@%s" $repo .digest -}}
{{- else if .tag -}}
{{- printf "%s:%s" $repo .tag -}}
{{- else -}}
{{- fail "neither digest nor tag is set: every image in this chart is pinned" -}}
{{- end -}}
{{- end -}}

{{- define "milvus.validate" -}}
{{- if not .Values.auth.rootPassword }}
{{- fail "milvus-standalone: auth.rootPassword is required — authorization is on, and a chart that fell back to Milvus's documented default would be worse than one that refuses to render" }}
{{- end }}
{{- if gt (len .Values.auth.rootPassword) 72 }}
{{- fail "milvus-standalone: auth.rootPassword is longer than the 72 characters Milvus accepts — it would be silently truncated and every client would then be refused" }}
{{- end }}
{{- if not .Values.persistence.enabled }}
{{- fail "milvus-standalone: persistence.enabled is false — segments, the write-ahead log and the embedded etcd all live on that volume, so without it a restarted pod comes back with an empty index and the deployment reads as 'the answers stopped being grounded'" }}
{{- end }}
{{- end -}}
