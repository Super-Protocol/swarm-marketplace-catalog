{{- define "swarm-model-server.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fixed rather than release-derived, like the other charts in this repository: the
object names are what a NOTES line, an ingress rule and a router's base URL all
have to agree on, and a release name chosen by the marketplace is not something
a listing's outputs can quote.
*/}}
{{- define "swarm-model-server.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "swarm-model-server.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "swarm-model-server.labels" -}}
helm.sh/chart: {{ include "swarm-model-server.chart" . }}
{{ include "swarm-model-server.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: model-server
{{- end -}}

{{- define "swarm-model-server.selectorLabels" -}}
app.kubernetes.io/name: {{ include "swarm-model-server.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "swarm-model-server.image" -}}
{{- $repo := printf "%s/%s" .Values.image.registry .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" $repo .Values.image.digest -}}
{{- else if .Values.image.tag -}}
{{- printf "%s:%s" $repo .Values.image.tag -}}
{{- else -}}
{{- fail "image.digest is empty and image.tag is not set: every image in this chart is pinned by digest" -}}
{{- end -}}
{{- end -}}

{{- define "swarm-model-server.secretName" -}}
{{- .Values.existingSecret | default (include "swarm-model-server.fullname" .) -}}
{{- end -}}

{{- define "swarm-model-server.secretKey" -}}
{{- if .Values.existingSecret -}}{{ .Values.existingSecretKey }}{{- else -}}api-key{{- end -}}
{{- end -}}

{{/*
Every way of deploying this chart that fails in the cluster rather than at
render time, refused here while there is still a human looking.
*/}}
{{- define "swarm-model-server.validate" -}}
{{- if not .Values.model.id -}}
{{- fail "model.id is empty: it is the name this endpoint advertises in /v1/models and the name a client asks for" -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9._-]*$" .Values.model.id) -}}
{{- fail (printf "model.id %q: a model id travels in a URL fragment and in an OpenAI request body, so it is limited to letters, digits, dot, underscore and hyphen" .Values.model.id) -}}
{{- end -}}

{{- if not .Values.gpu.enabled -}}
{{- fail "gpu.enabled is false: the published vLLM image is a CUDA build and does not serve on a CPU. A pod deployed this way starts, finds no device and restarts for ever" -}}
{{- end -}}
{{- if lt (int .Values.gpu.count) 1 -}}
{{- fail "gpu.count must be at least 1" -}}
{{- end -}}

{{- $w := .Values.model.weights -}}
{{- if not $w.repo -}}
{{- fail "model.weights.repo is empty: there is nothing to fetch the weights from" -}}
{{- end -}}
{{- if not $w.revision -}}
{{- fail "model.weights.revision is empty: a branch name is not a pin, and `main` moves. Give the commit sha the manifest was built from" -}}
{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{40}$" $w.revision) -}}
{{- fail (printf "model.weights.revision %q is not a 40-character commit sha" $w.revision) -}}
{{- end -}}
{{- if not $w.files -}}
{{- fail "model.weights.files is empty: this chart will not serve weights it cannot verify. Build a manifest with scripts/model-weights-manifest.py" -}}
{{- end -}}
{{- $sum := 0 -}}
{{- range $i, $f := $w.files -}}
{{- if not $f.path -}}{{- fail (printf "model.weights.files[%d] has no path" $i) -}}{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString ($f.sha256 | default ""))) -}}
{{- fail (printf "model.weights.files[%d] (%s) has no sha256: an unverifiable file is the whole problem this manifest exists to solve" $i $f.path) -}}
{{- end -}}
{{- if not $f.size -}}{{- fail (printf "model.weights.files[%d] (%s) has no size" $i $f.path) -}}{{- end -}}
{{- $sum = add $sum (int64 $f.size) -}}
{{- end -}}
{{- if ne (int64 $sum) (int64 $w.totalBytes) -}}
{{- fail (printf "model.weights.totalBytes is %d but the files add up to %d: the manifest was edited by hand" (int64 $w.totalBytes) (int64 $sum)) -}}
{{- end -}}

{{/* The volume has to hold the weights plus the `.part` file of whichever one is
     being written, and a volume that is merely nearly big enough fails after the
     download rather than before it. */}}
{{- if .Values.persistence.enabled -}}
{{- $capacity := include "swarm-model-server.storageBytes" . | int64 -}}
{{- $needed := add (int64 $w.totalBytes) (div (int64 $w.totalBytes) 10) | int64 -}}
{{- if lt (int64 $capacity) (int64 $needed) -}}
{{- fail (printf "persistence.size %s holds %d bytes but the weights need %d (%s plus a tenth for the file being written): the download would fail with the volume full" .Values.persistence.size (int64 $capacity) (int64 $needed) (include "swarm-model-server.humanBytes" (dict "n" $w.totalBytes))) -}}
{{- end -}}
{{- end -}}

{{- if .Values.model.toolCalling.enabled -}}
{{- $parsers := include "swarm-model-server.toolParsers" . | fromJsonArray -}}
{{- if not (has .Values.model.toolCalling.parser $parsers) -}}
{{- fail (printf "model.toolCalling.parser %q is not one of the parsers vLLM %s registers: %s. A parser that does not match the model's output format turns every tool call into prose" .Values.model.toolCalling.parser .Chart.AppVersion (join ", " $parsers)) -}}
{{- end -}}
{{- end -}}

{{/* The trap this chart was caught by once. A listing that sets a template
     inline renders here and is refused by the marketplace's publish parse, which
     is a long way from the person who wrote it — so it is refused here instead,
     where the message can say what to do. */}}
{{- if .Values.model.chatTemplate -}}
{{- fail "model.chatTemplate is gone: a chat template is Jinja, and a listing cannot carry `{{ }}` because the marketplace parses it as its own expression language. Put the template in charts/swarm-model-server/files/chat-templates/ and name it with model.chatTemplateFile" -}}
{{- end -}}
{{- if .Values.model.chatTemplateFile -}}
{{- $file := printf "files/chat-templates/%s" .Values.model.chatTemplateFile -}}
{{- if not (.Files.Get $file) -}}
{{- fail (printf "model.chatTemplateFile %q does not exist: %s is not in the chart, so the engine would start with no template at all" .Values.model.chatTemplateFile $file) -}}
{{- end -}}
{{- end -}}

{{- if .Values.ingress.enabled -}}
{{- if not .Values.hostname -}}
{{- fail "hostname is empty and the ingress is on: there is no name to serve on" -}}
{{- end -}}
{{- if not (or .Values.apiKey .Values.existingSecret) -}}
{{- fail "the ingress is on and no apiKey is set: this chart does not publish an unauthenticated inference endpoint on a public hostname, which would hand the deployment's GPU to whoever found the name (marketplace trap 13)" -}}
{{- end -}}
{{- if and .Values.ingress.tls.enabled (not .Values.ingress.tls.secretName) -}}
{{- fail "ingress.tls.enabled is true but ingress.tls.secretName is empty" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
`persistence.size` as bytes. Helm has no quantity parser, so the suffixes this
chart actually accepts are spelled out and anything else is refused rather than
silently read as a byte count.
*/}}
{{- define "swarm-model-server.storageBytes" -}}
{{- $s := .Values.persistence.size | toString -}}
{{- $units := dict "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 -}}
{{- $out := "" -}}
{{- range $suffix, $factor := $units -}}
{{- if hasSuffix $suffix $s -}}
{{- $n := trimSuffix $suffix $s -}}
{{- if not (regexMatch "^[0-9]+$" $n) -}}
{{- fail (printf "persistence.size %q: a fractional quantity is not supported here, give whole %s" $s $suffix) -}}
{{- end -}}
{{- $out = mul (int64 $n) (int64 $factor) | toString -}}
{{- end -}}
{{- end -}}
{{- if not $out -}}
{{- if regexMatch "^[0-9]+$" $s -}}
{{- $out = $s -}}
{{- else -}}
{{- fail (printf "persistence.size %q: expected a whole number of bytes or a Ki/Mi/Gi/Ti quantity" $s) -}}
{{- end -}}
{{- end -}}
{{- $out -}}
{{- end -}}

{{- define "swarm-model-server.humanBytes" -}}
{{- printf "%.2f GiB" (divf (float64 .n) 1073741824.0) -}}
{{- end -}}

{{/*
The engine command line. Collected in one place because what it says is the
whole behaviour of the deployment, and because the arguments are rendered into
the manifests — so anything secret must not be here (marketplace trap 4).
*/}}
{{- define "swarm-model-server.engineArgs" -}}
{{- $args := list "--model" .Values.model.dir "--served-model-name" .Values.model.id -}}
{{- $args = concat $args (list "--host" "0.0.0.0" "--port" (.Values.service.targetPort | toString)) -}}
{{- if .Values.model.maxModelLen -}}
{{- $args = concat $args (list "--max-model-len" (.Values.model.maxModelLen | toString)) -}}
{{- end -}}
{{- if .Values.model.gpuMemoryUtilization -}}
{{- $args = concat $args (list "--gpu-memory-utilization" (.Values.model.gpuMemoryUtilization | toString)) -}}
{{- end -}}
{{- if .Values.model.dtype -}}
{{- $args = concat $args (list "--dtype" (.Values.model.dtype | toString)) -}}
{{- end -}}
{{- if .Values.model.quantization -}}
{{- $args = concat $args (list "--quantization" (.Values.model.quantization | toString)) -}}
{{- end -}}
{{- if gt (int .Values.gpu.count) 1 -}}
{{- $args = concat $args (list "--tensor-parallel-size" (.Values.gpu.count | toString)) -}}
{{- end -}}
{{- if .Values.model.toolCalling.enabled -}}
{{- $args = concat $args (list "--enable-auto-tool-choice" "--tool-call-parser" .Values.model.toolCalling.parser) -}}
{{- end -}}
{{- if .Values.model.chatTemplateFile -}}
{{- $args = concat $args (list "--chat-template" "/etc/model/chat-template.jinja") -}}
{{- end -}}
{{- $args = concat $args (.Values.model.extraArgs | default list) -}}
{{- toJson $args -}}
{{- end -}}

{{/*
Every tool-call parser vLLM {{ .Chart.AppVersion }} registers
(`vllm/tool_parsers/__init__.py`, `_TOOL_PARSERS_TO_REGISTER`). The engine
itself refuses an unknown name at startup, which costs a deploy and a pod log to
discover; refusing it here costs a render. Regenerate this list when
`appVersion` moves — `scripts/vllm-tool-parsers.py` prints it.
*/}}
{{- define "swarm-model-server.toolParsers" -}}
{{- toJson (list
  "apertus" "cohere_command3" "cohere_command4" "deepseek_v3" "deepseek_v31" "deepseek_v32"
  "deepseek_v4" "ernie45" "functiongemma" "gemma4" "gigachat3" "glm45"
  "glm47" "granite" "granite4" "hermes" "hunyuan_a13b" "hy_v3"
  "internlm" "jamba" "kimi_k2" "lfm2" "llama3_json" "llama4_json"
  "llama4_pythonic" "longcat" "mimo" "minimax" "minimax_m2" "mistral"
  "olmo3" "openai" "phi4_mini_json" "poolside_v1" "pythonic" "qwen3_coder"
  "qwen3_xml" "seed_oss" "step3" "step3p5" "xlam"
) -}}
{{- end -}}
