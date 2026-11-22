{{- define "swarm-doc-ingest.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fixed rather than release-derived, like every other chart here: the Service name
is what a composing listing's other components reach from a `base` value and
what its `outputs` quote, and a release name chosen by the marketplace is not
something a listing can write down in advance.
*/}}
{{- define "swarm-doc-ingest.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "swarm-doc-ingest.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "swarm-doc-ingest.labels" -}}
helm.sh/chart: {{ include "swarm-doc-ingest.chart" . }}
{{ include "swarm-doc-ingest.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: doc-ingest
{{- end -}}

{{- define "swarm-doc-ingest.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarm-doc-ingest.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "swarm-doc-ingest.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else -}}
{{- fail "image.digest is empty: the image this chart runs is ours and is pinned by digest, never by tag alone" -}}
{{- end -}}
{{- end -}}

{{/*
The secret the key is read from: the chart's own, or one the listing already
created. Either way the key reaches the container through `secretKeyRef` and
never through an argument or an env value — a command line is rendered into the
manifests and published in the evidence snapshot in clear.
*/}}
{{- define "swarm-doc-ingest.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- include "swarm-doc-ingest.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
`OMP_NUM_THREADS`, from the pod's own CPU quota: the core count rounded up,
never below 1.

The quota is a Kubernetes quantity, so `4`, `1.5` and `500m` are three legal
ways to write it, and stripping the `m` read `500m` as 500 threads — half a core
running 500 OpenMP threads is the slowest extraction this chart can produce,
which is the opposite of what the number is here for. Millicores are divided,
not stripped. A fraction rounds up because 0 is not a thread count, and because
the alternative — OpenMP's own default of every core the node has — is the
oversubscription this value exists to prevent.
*/}}
{{- define "swarm-doc-ingest.ompNumThreads" -}}
{{- if .Values.ompNumThreads -}}
{{- .Values.ompNumThreads -}}
{{- else -}}
{{- $cpu := .Values.resources.cpu | toString -}}
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
Render-time refusals. Each of these is a configuration that would otherwise
deploy and then fail at boot or, worse, quietly extract less than the numbers in
the chart's README say it does.
*/}}
{{- define "swarm-doc-ingest.validate" -}}
{{- $layouts := list "layout_heron_default" "layout_heron_101" "layout_egret_medium" "layout_egret_large" "layout_egret_xlarge" -}}
{{- if not (has .Values.extraction.layoutPreset $layouts) -}}
{{- fail (printf "extraction.layoutPreset %q is not a Docling preset; one of: %s" .Values.extraction.layoutPreset (join ", " $layouts)) -}}
{{- end -}}
{{- /*
One preset, no acknowledgement. `scripts/prefetch-models.py` in the source
repository bakes `docling-layout-heron` and nothing else, and the ConfigMap sets
`HF_HUB_OFFLINE=1`, so a pod asked for an egret preset cannot fetch the weights
and cannot start: the only outcome the old `acknowledgeUnmeasuredLayout=true`
escape hatch could reach was a CrashLoopBackOff. Serving another preset is an
image change — new weights, new digest, new bake-off numbers — and a chart
value cannot stand in for one.
*/ -}}
{{- if ne .Values.extraction.layoutPreset "layout_heron_default" -}}
{{- fail (printf "extraction.layoutPreset is %q. This image bakes layout_heron_default alone and runs with HF_HUB_OFFLINE=1, so the weights cannot be fetched and the pod would never start. The measurement that chose it: egret_large scores 0.000 on a table drawn without ruling lines, where heron scores 0.968. Another preset needs a swarm-doc-ingest release that bakes its weights, fresh bakeoff/bench.py numbers, and a new image digest here." .Values.extraction.layoutPreset) -}}
{{- end -}}
{{- if not (has .Values.extraction.tableMode (list "fast" "accurate")) -}}
{{- fail (printf "extraction.tableMode %q: one of fast, accurate" .Values.extraction.tableMode) -}}
{{- end -}}
{{- if not (has .Values.extraction.ocrEngine (list "rapidocr" "off")) -}}
{{- fail (printf "extraction.ocrEngine %q: this image bakes RapidOCR only, so one of rapidocr, off" .Values.extraction.ocrEngine) -}}
{{- end -}}
{{- if and (not .Values.apiKey) (not .Values.existingSecret) -}}
{{- fail "apiKey is empty and existingSecret is not set: a namespace is not a trust boundary once a second workload is in it" -}}
{{- end -}}
{{- if and .Values.apiKey .Values.existingSecret -}}
{{- fail "apiKey and existingSecret are both set: which one wins would be a property of this template rather than a decision" -}}
{{- end -}}
{{- if gt (int .Values.replicaCount) 1 -}}
{{- if not .Values.workVolume.sizeLimit -}}
{{- fail "replicaCount > 1 with no workVolume.sizeLimit: each replica writes uploads to its own volume and an unbounded one fills the node" -}}
{{- end -}}
{{- end -}}
{{- end -}}
