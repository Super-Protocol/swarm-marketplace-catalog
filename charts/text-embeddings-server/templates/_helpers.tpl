{{/*
Object names. Derived from `name`, not from the release: a listing reaches a Service from
another component's `base` values, and a release-derived name is a name the other component
cannot write down.
*/}}
{{- define "tes.name" -}}
{{- required "text-embeddings-server: name is required — two releases of this chart in one namespace would otherwise share every object, and one endpoint serving the other's model is invisible in a render" .Values.name -}}
{{- end -}}

{{- define "tes.labels" -}}
app.kubernetes.io/name: {{ include "tes.name" . }}
app.kubernetes.io/component: text-embeddings-server
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "tes.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tes.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
An image reference, by digest where one is declared.

Same rule and same helper shape as charts/swarm-model-server: a tag names a release and a
digest names the bytes, and only one of those is what the deployment attests to.
*/}}
{{- define "tes.imageRef" -}}
{{- $repo := printf "%s/%s" .registry .repository -}}
{{- if .digest -}}
{{- printf "%s@%s" $repo .digest -}}
{{- else if .tag -}}
{{- printf "%s:%s" $repo .tag -}}
{{- else -}}
{{- fail "neither digest nor tag is set: every image in this chart is pinned" -}}
{{- end -}}
{{- end -}}

{{/* The selected preset, resolved once. */}}
{{- define "tes.preset" -}}
{{- $slug := required "text-embeddings-server: model.preset is required — it decides whether this process is an embedder or a reranker" .Values.model.preset -}}
{{- $preset := index .Values.model.presets $slug -}}
{{- if not $preset -}}
{{- fail (printf "text-embeddings-server: no preset %q. Measured presets: %s. A preset is added by building its manifest with scripts/model-weights-manifest.py and measuring the endpoint, not by naming a repository here" $slug (keys .Values.model.presets | sortAlpha | join ", ")) -}}
{{- end -}}
{{- $preset | toJson -}}
{{- end -}}

{{/*
The container's CPU and memory: the chart's own `resources` where an operator set one,
otherwise the selected preset's measured `sizing.resources`.

The explicit `nvidia.com/gpu: 0` is added either way. A cluster space with a GPU has a
LimitRange that defaults one in, and a CPU component that quietly acquired the card is a
generation engine left Pending on a GPU quota of one.
*/}}
{{- define "tes.resources" -}}
{{- $p := include "tes.preset" . | fromJson -}}
{{- $r := .Values.resources -}}
{{- if not $r -}}
{{- $r = dict "requests" (dict "cpu" $p.sizing.resources.cpu "memory" $p.sizing.resources.memory) "limits" (dict "cpu" $p.sizing.resources.cpu "memory" $p.sizing.resources.memory) -}}
{{- end -}}
{{- $out := dict -}}
{{- range $section := list "requests" "limits" -}}
{{- $values := get $r $section | default dict -}}
{{- $_ := set $out $section (merge (dict "nvidia.com/gpu" "0") $values) -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/*
How many threads the backend may start, which is NOT how many it starts by itself.

The server sizes its pools from the HOST's core count — `std::thread::available_parallelism`
for rayon, the OpenMP runtime for the GEMMs — and a container does not change that number. On
a two-core limit on an eight-core laptop that oversubscription is survivable; on the same limit
on a GPU node with a hundred and twenty-eight cores it is not: the pools start a thread per host
core, the cgroup gives the pod two, and the result is throttling and context switching rather
than work. Measured on 2026-11-22 with twelve 215-token passages through BGE Reranker v2 M3 at
`cpu: 2`: 7.6 s with the pools pinned, 10.8 s unpinned on an eight-core host, and over 60 s
unpinned in the cluster — past the retriever's rerank timeout, which silently dropped the scores
and served citations reading 0.0.

Rounded UP from the limit and never zero, and millicores are divided rather than stripped —
`500m` read as 500 threads is the oversubscription this value exists to remove. Same arithmetic
and the same override as `swarm-doc-ingest.ompNumThreads`, which SUP-882 landed for the same
reason in the ingestion chart beside this one; a reader comparing the two charts should find one
convention, not two.
*/}}
{{- define "tes.threads" -}}
{{- if .Values.threads -}}
{{- .Values.threads -}}
{{- else -}}
{{- $p := include "tes.preset" . | fromJson -}}
{{- $cpu := $p.sizing.resources.cpu -}}
{{- if and .Values.resources .Values.resources.limits .Values.resources.limits.cpu -}}
{{- $cpu = .Values.resources.limits.cpu -}}
{{- end -}}
{{- $cpu = toString $cpu -}}
{{- $cores := 0.0 -}}
{{- if hasSuffix "m" $cpu -}}
{{- $cores = divf (float64 (trimSuffix "m" $cpu)) 1000.0 -}}
{{- else -}}
{{- $cores = float64 $cpu -}}
{{- end -}}
{{- max (ceil $cores) 1 -}}
{{- end -}}
{{- end -}}

{{/*
Refuses the configurations that render cleanly and then fail in the cluster, each with a
sentence that says what to do. Every one of these has an analogue in
charts/swarm-model-server, and for the same reason: a render error costs a minute, a
deployment that comes up and answers nothing costs an afternoon.
*/}}
{{- define "tes.validate" -}}
{{- $p := include "tes.preset" . | fromJson -}}
{{- if not .Values.apiKey }}
{{- fail "text-embeddings-server: apiKey is required — the endpoint authenticates, and its caller has to carry a key regardless" }}
{{- end }}
{{- if not (has $p.task (list "embed" "rerank")) }}
{{- fail (printf "text-embeddings-server: preset task %q is neither embed nor rerank" $p.task) }}
{{- end }}
{{- if and (eq $p.task "embed") (le ($p.dimensions | int) 0) }}
{{- fail "text-embeddings-server: an embed preset must declare the width this checkpoint really returns — the collection's vector field is created from it and cannot be re-sized afterwards" }}
{{- end }}
{{- if not (regexMatch "^[A-Za-z0-9._-]+$" (toString $p.id)) }}
{{- fail (printf "text-embeddings-server: preset id %q — letters, digits, dot, underscore and hyphen only" (toString $p.id)) }}
{{- end }}
{{- if not $p.licence }}
{{- fail (printf "text-embeddings-server: preset %s declares no licence — the terms a deployment serves under belong in its evidence" .Values.model.preset) }}
{{- end }}
{{- if not $p.sizing }}
{{- fail (printf "text-embeddings-server: preset %s declares no sizing — the batch ceiling and the memory demand are properties of the checkpoint's sequence length, and the obvious values are wrong for an 8192-token model (it is OOMKilled during warm-up at 16384 batch tokens under an 8Gi limit). Measure them" .Values.model.preset) }}
{{- end }}
{{- $w := $p.weights }}
{{- if not $w.files }}
{{- fail "text-embeddings-server: weights.files is empty — build it with scripts/model-weights-manifest.py <repo>" }}
{{- end }}
{{- if not (regexMatch "^[0-9a-f]{40}$" (toString $w.revision)) }}
{{- fail (printf "text-embeddings-server: weights.revision %q is not a commit — a branch or a tag can be moved under a deployment that has already been attested" (toString $w.revision)) }}
{{- end }}
{{- $sum := 0 }}
{{- $safetensors := false }}
{{- range $w.files }}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString .sha256)) }}
{{- fail (printf "text-embeddings-server: %s has no sha256 — an unpinned file is a file the deployment cannot prove it served" .path) }}
{{- end }}
{{- if hasSuffix ".safetensors" .path }}{{ $safetensors = true }}{{ end }}
{{- $sum = add $sum (int64 .size) }}
{{- end }}
{{- if not $safetensors }}
{{- fail "text-embeddings-server: the manifest names no .safetensors file, and this backend loads nothing else — a checkpoint published only as pytorch_model.bin cannot be served here" }}
{{- end }}
{{- if ne ($sum | int64) ($w.totalBytes | int64) }}
{{- fail (printf "text-embeddings-server: weights.files add up to %d bytes, totalBytes says %d — one of them is stale" ($sum | int64) ($w.totalBytes | int64)) }}
{{- end }}
{{- $volume := include "tes.volumeBytes" . | int64 }}
{{- if and .Values.persistence.enabled (lt $volume ($w.totalBytes | int64)) }}
{{- fail (printf "text-embeddings-server: persistence.size holds %d bytes and the weights need %d — the fetcher fills the volume and the pod never becomes ready" $volume ($w.totalBytes | int64)) }}
{{- end }}
{{- end -}}

{{/*
The weights volume, in bytes.

Left unset, it is the manifest's own total plus a tenth for the file being written, rounded
up to whole GiB — so a listing whose model is a parameter does not have to write one number
that is wrong for every option but the largest.
*/}}
{{- define "tes.volumeBytes" -}}
{{- $s := .Values.persistence.size | toString -}}
{{- if eq $s "" -}}
{{- $p := include "tes.preset" . | fromJson -}}
{{- $need := mulf ($p.weights.totalBytes | float64) 1.1 -}}
{{- $gib := divf $need 1073741824 | float64 -}}
{{- mulf (ceil $gib) 1073741824 | int64 -}}
{{- else if hasSuffix "Gi" $s }}{{ mulf (trimSuffix "Gi" $s | float64) 1073741824 | int64 }}
{{- else if hasSuffix "Mi" $s }}{{ mulf (trimSuffix "Mi" $s | float64) 1048576 | int64 }}
{{- else if hasSuffix "Ti" $s }}{{ mulf (trimSuffix "Ti" $s | float64) 1099511627776 | int64 }}
{{- else }}{{ $s | int64 }}
{{- end }}
{{- end -}}

{{/* The same number as a Kubernetes quantity. */}}
{{- define "tes.volumeSize" -}}
{{- $bytes := include "tes.volumeBytes" . | int64 -}}
{{- printf "%dGi" (divf ($bytes | float64) 1073741824 | int64) -}}
{{- end -}}
