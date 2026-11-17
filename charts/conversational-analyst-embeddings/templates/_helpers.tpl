{{- define "analyst-embeddings.name" -}}
analyst-embeddings
{{- end -}}

{{- define "analyst-embeddings.labels" -}}
app.kubernetes.io/name: {{ include "analyst-embeddings.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "analyst-embeddings.selectorLabels" -}}
app.kubernetes.io/name: {{ include "analyst-embeddings.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Refuses the configurations that render cleanly and then fail in the cluster, each with a
sentence that says what to do. Every one of these has an analogue in
charts/swarm-model-server, and for the same reason: a render error costs a minute, a
deployment that comes up and answers nothing costs an afternoon.
*/}}
{{- define "analyst-embeddings.validate" -}}
{{- if not .Values.apiKey }}
{{- fail "conversational-analyst-embeddings: apiKey is required — the endpoint authenticates, and the agent's OpenAI client has to carry a key regardless" }}
{{- end }}
{{- $w := .Values.model.weights }}
{{- if not $w.files }}
{{- fail "conversational-analyst-embeddings: model.weights.files is empty — build it with scripts/model-weights-manifest.py <repo>" }}
{{- end }}
{{- if not (regexMatch "^[0-9a-f]{40}$" (toString $w.revision)) }}
{{- fail (printf "conversational-analyst-embeddings: model.weights.revision %q is not a commit — a branch or a tag can be moved under a deployment that has already been attested" (toString $w.revision)) }}
{{- end }}
{{- $sum := 0 }}
{{- range $w.files }}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString .sha256)) }}
{{- fail (printf "conversational-analyst-embeddings: %s has no sha256 — an unpinned file is a file the deployment cannot prove it served" .path) }}
{{- end }}
{{- $sum = add $sum (int64 .size) }}
{{- end }}
{{- if ne ($sum | int64) ($w.totalBytes | int64) }}
{{- fail (printf "conversational-analyst-embeddings: model.weights.files add up to %d bytes, totalBytes says %d — one of them is stale" ($sum | int64) ($w.totalBytes | int64)) }}
{{- end }}
{{- $volume := include "analyst-embeddings.volumeBytes" . | int64 }}
{{- if and .Values.persistence.enabled (lt $volume ($w.totalBytes | int64)) }}
{{- fail (printf "conversational-analyst-embeddings: persistence.size holds %d bytes and the weights need %d — the fetcher fills the volume and the pod never becomes ready" $volume ($w.totalBytes | int64)) }}
{{- end }}
{{- if not (regexMatch "^[A-Za-z0-9._-]+$" (toString .Values.model.id)) }}
{{- fail (printf "conversational-analyst-embeddings: model.id %q — letters, digits, dot, underscore and hyphen only" (toString .Values.model.id)) }}
{{- end }}
{{- if le (.Values.model.dimensions | int) 0 }}
{{- fail "conversational-analyst-embeddings: model.dimensions must be the width this checkpoint really returns — the knowledge index's vector column is created from it and cannot be re-sized afterwards" }}
{{- end }}
{{- end -}}

{{/* persistence.size in bytes, for the volume check above. */}}
{{- define "analyst-embeddings.volumeBytes" -}}
{{- $s := .Values.persistence.size | toString -}}
{{- if hasSuffix "Gi" $s }}{{ mulf (trimSuffix "Gi" $s | float64) 1073741824 | int64 }}
{{- else if hasSuffix "Mi" $s }}{{ mulf (trimSuffix "Mi" $s | float64) 1048576 | int64 }}
{{- else if hasSuffix "Ti" $s }}{{ mulf (trimSuffix "Ti" $s | float64) 1099511627776 | int64 }}
{{- else }}{{ $s | int64 }}
{{- end }}
{{- end -}}
