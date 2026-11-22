{{- define "crag.name" -}}
{{- required "confidential-rag: name is required" .Values.name -}}
{{- end -}}

{{- define "crag.labels" -}}
app.kubernetes.io/name: {{ include "crag.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Per-component selector labels: four Deployments in one chart. */}}
{{- define "crag.selectorLabels" -}}
app.kubernetes.io/name: {{ include "crag.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "crag.imageRef" -}}
{{- $repo := printf "%s/%s" .registry .repository -}}
{{- if .digest -}}
{{- printf "%s@%s" $repo .digest -}}
{{- else if .tag -}}
{{- printf "%s:%s" $repo .tag -}}
{{- else -}}
{{- fail "neither digest nor tag is set: every image in this chart is pinned" -}}
{{- end -}}
{{- end -}}

{{/*
Refuses the configurations that render cleanly and then fail in the cluster.

The first two are the ones worth having: a console with no password is an open door on a
public hostname, and a top-k above the candidate list is a search Milvus refuses at query
time — which reads as "the answers stopped being grounded" rather than as a misconfiguration.
*/}}
{{- define "crag.validate" -}}
{{- if not .Values.console.password }}
{{- fail "confidential-rag: console.password is required — this deployment is served on a public hostname and the console is the whole of what stands in front of the corpus" }}
{{- end }}
{{- if not .Values.console.sessionKey }}
{{- fail "confidential-rag: console.sessionKey is required — it signs the session cookie, and a fixed or empty key means anybody can mint one" }}
{{- end }}
{{- if lt (len .Values.console.sessionKey) 32 }}
{{- fail "confidential-rag: console.sessionKey is shorter than 32 characters" }}
{{- end }}
{{- if not .Values.hostname }}
{{- fail "confidential-rag: hostname is required — it is where the console is served" }}
{{- end }}
{{- if not .Values.ingress.className }}
{{- fail "confidential-rag: ingress.className is required — an Ingress without one is created, registered, has DNS written for it, and is served by nothing" }}
{{- end }}
{{- if not .Values.inference.model }}
{{- fail "confidential-rag: inference.model is required — it is the name the generation endpoint advertises, and a wrong one is a 404 on every answer" }}
{{- end }}
{{- if not .Values.embeddings.model }}
{{- fail "confidential-rag: embeddings.model is required" }}
{{- end }}
{{- if and .Values.reranker.enabled (not .Values.reranker.model) }}
{{- fail "confidential-rag: reranker.model is required while reranker.enabled is true" }}
{{- end }}
{{- if not .Values.vectorStore.password }}
{{- fail "confidential-rag: vectorStore.password is required — the vector store authenticates" }}
{{- end }}
{{- if le (.Values.embeddings.dimensions | int) 0 }}
{{- fail "confidential-rag: embeddings.dimensions must be the width the embedder really returns — the collection's vector field is created from it and cannot be re-sized once it holds anything" }}
{{- end }}
{{- if gt (.Values.server.topK | int) (.Values.server.ef | int) }}
{{- fail (printf "confidential-rag: server.topK (%d) is above server.ef (%d) — Milvus refuses a search whose top-k exceeds its candidate list, and every answer then comes back ungrounded" (.Values.server.topK | int) (.Values.server.ef | int)) }}
{{- end }}
{{- if and .Values.reranker.enabled (gt (.Values.server.topK | int) (.Values.reranker.maxClientBatchSize | int)) }}
{{- fail (printf "confidential-rag: server.topK (%d) is above the reranker's client batch ceiling (%d) — Text Embeddings Inference answers a bigger batch with `413 batch size %d > maximum allowed batch size %d`, the adapter falls back to the retriever's own order, and the only symptom is citations with no relevance score. Every query would be unreranked while the listing still advertises a reranker" (.Values.server.topK | int) (.Values.reranker.maxClientBatchSize | int) (.Values.server.topK | int) (.Values.reranker.maxClientBatchSize | int)) }}
{{- end }}
{{- if gt (.Values.server.rerankerTopK | int) (.Values.server.topK | int) }}
{{- fail "confidential-rag: server.rerankerTopK is above server.topK — the reranker cannot return more documents than the retriever found" }}
{{- end }}
{{- if hasSuffix "/v1" .Values.reranker.url }}
{{- fail "confidential-rag: reranker.url ends in /v1 — Text Embeddings Inference serves reranking at /rerank, at the root, and the /v1 prefix belongs to the embedding endpoint only" }}
{{- end }}
{{- if not (hasSuffix "/v1" .Values.embeddings.url) }}
{{- fail "confidential-rag: embeddings.url must end in /v1 — the OpenAI client appends /embeddings to it" }}
{{- end }}
{{- end -}}
