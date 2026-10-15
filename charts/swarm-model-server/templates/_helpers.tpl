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
The engines this deployment runs, normalised into one list every template walks.

Two value shapes reach this chart and both end here.

`model:` is one engine with the card to itself, and renders exactly what it
rendered before 0.3.0 — byte for byte, so the three listings on that path keep
the evidence digest they publish today.

`engines:` is one entry per engine *sharing* the single `nvidia.com/gpu: 1` this
pod holds (SUP-332). Kubernetes allocates a GPU to a container and not to a pod,
so engines that share a card share a container: one `vllm serve` process each,
started by `files/run-engines.py`, each with its own weights directory, its own
port, its own URL prefix and its own explicitly budgeted share of the card's
memory.

Returned by mutating the `out` dict the caller passes rather than through
`toJson`: a JSON round trip turns a weight file's `size` into 1.0001462368e+10,
and the fetcher compares that field to a byte count.

    {{- $e := dict -}}
    {{- include "swarm-model-server.engines" (dict "ctx" . "out" $e) -}}
    {{- $engines := $e.engines -}}
*/}}
{{- define "swarm-model-server.engines" -}}
{{- $ctx := .ctx -}}
{{- $fullname := include "swarm-model-server.fullname" $ctx -}}
{{- $declared := $ctx.Values.engines | default list -}}
{{- $engines := list -}}
{{- if $declared -}}
{{- range $i, $entry := $declared -}}
{{- $name := $entry.name | default "" | toString -}}
{{- $model := $entry.model | default dict -}}
{{- $template := $model.chatTemplateFile | default "" -}}
{{- $port := int ($entry.port | default (add (int $ctx.Values.service.targetPort) $i)) -}}
{{- $engines = append $engines (dict
      "name" $name
      "index" $i
      "path" (printf "engines[%d]" $i)
      "model" $model
      "dir" (printf "/models/%s" $name)
      "port" $port
      "servicePort" $port
      "portName" $name
      "prefix" (printf "/%s" $name)
      "container" "vllm"
      "fetcher" (printf "fetch-weights-%s" $name)
      "manifestPath" (printf "/opt/engines/manifest-%s.json" $name)
      "chatTemplatePath" (ternary (printf "/opt/engines/chat-template-%s.jinja" $name) "" (ne $template ""))
      "cacheRoot" (printf "/cache/%s" $name)
   ) -}}
{{- end -}}
{{- else -}}
{{- $model := $ctx.Values.model -}}
{{- $template := $model.chatTemplateFile | default "" -}}
{{- $engines = list (dict
      "name" ""
      "index" 0
      "path" "model"
      "model" $model
      "dir" $model.dir
      "port" (int $ctx.Values.service.targetPort)
      "servicePort" (int $ctx.Values.service.port)
      "portName" "http"
      "prefix" ""
      "container" "vllm"
      "fetcher" "fetch-weights"
      "manifestPath" "/etc/model/manifest.json"
      "chatTemplatePath" (ternary "/etc/model/chat-template.jinja" "" (ne $template ""))
      "cacheRoot" "/cache"
   ) -}}
{{- end -}}
{{- $_ := set .out "engines" $engines -}}
{{- $_ := set .out "shared" (gt (len $engines) 1) -}}
{{- $_ := set .out "multi" (not (empty $declared)) -}}
{{- end -}}

{{/*
Every way of deploying this chart that fails in the cluster rather than at
render time, refused here while there is still a human looking.
*/}}
{{- define "swarm-model-server.validate" -}}
{{- $e := dict -}}
{{- include "swarm-model-server.engines" (dict "ctx" . "out" $e) -}}
{{- $engines := $e.engines -}}

{{- if and .Values.engines .Values.model.id -}}
{{- fail "model.id and engines are both set: one card is served either by the single `model` block or by the `engines` list, never by both. Move the model into an `engines` entry, or empty the list" -}}
{{- end -}}

{{- if not .Values.gpu.enabled -}}
{{- fail "gpu.enabled is false: the published vLLM image is a CUDA build and does not serve on a CPU. A pod deployed this way starts, finds no device and restarts for ever" -}}
{{- end -}}
{{- if lt (int .Values.gpu.count) 1 -}}
{{- fail "gpu.count must be at least 1" -}}
{{- end -}}
{{- if and $e.shared (ne (int .Values.gpu.count) 1) -}}
{{- fail (printf "gpu.count is %d and %d engines share the card: each engine would claim every device for its own tensor-parallel group, and the first one to finish loading would hold memory the others counted on. Serve several models from one device, or one model from several" (int .Values.gpu.count) (len $engines)) -}}
{{- end -}}

{{/* Names, ports and model ids are what the Service, the Ingress and an OpenAI
     request body tell the engines apart by; two of anything here is a
     deployment where one of the two models is unreachable. */}}
{{- $names := list -}}
{{- $ports := list -}}
{{- $ids := list -}}
{{- range $engine := $engines -}}
{{- include "swarm-model-server.validateEngine" (dict "ctx" $ "engine" $engine "multi" $e.multi "shared" $e.shared) -}}
{{- if $e.multi -}}
{{- if has $engine.name $names -}}
{{- fail (printf "two engines are named %q: the name is the URL prefix each model is served under and the port name the Service routes to, so it has to be unique" $engine.name) -}}
{{- end -}}
{{- if has (int $engine.port) $ports -}}
{{- fail (printf "two engines ask for port %d: one of them would fail to bind and the pod would restart for ever" (int $engine.port)) -}}
{{- end -}}
{{- if has $engine.model.id $ids -}}
{{- fail (printf "two engines serve model id %q: a client names the model in the request body, so the duplicate is unaddressable" $engine.model.id) -}}
{{- end -}}
{{- $names = append $names $engine.name -}}
{{- $ports = append $ports (int $engine.port) -}}
{{- $ids = append $ids $engine.model.id -}}
{{- end -}}
{{- end -}}

{{/* The memory budget, which is the whole reason two engines on one card is
     expressible at all. vLLM reads `--gpu-memory-utilization` as a fraction of
     the card's *total* memory and claims it for weights plus KV cache, so two
     engines fit exactly when their fractions do. The remainder below 1.0 is not
     spare: the driver's own allocation and whatever the second CUDA context
     costs over the first live there, and an engine that finds less free memory
     than it was promised refuses to start rather than serving a short cache. */}}
{{- if $e.shared -}}
{{- $sum := 0.0 -}}
{{- range $engine := $engines -}}
{{- $sum = addf $sum (float64 $engine.model.gpuMemoryUtilization) -}}
{{- end -}}
{{- if gt $sum 0.95 -}}
{{- fail (printf "the %d engines' gpuMemoryUtilization adds up to %.2f: they share one card, so their fractions share one 1.0 — and the last 0.05 is the margin the driver and the second CUDA context need. Budget them below 0.95" (len $engines) $sum) -}}
{{- end -}}
{{- end -}}

{{/* The volume has to hold every engine's weights plus the `.part` file of
     whichever one is being written, and a volume that is merely nearly big
     enough fails after the download rather than before it. */}}
{{- $sumBytes := int64 0 -}}
{{- range $engine := $engines -}}
{{- $sumBytes = add (int64 $sumBytes) (int64 (($engine.model.weights | default dict).totalBytes | default 0)) -}}
{{- end -}}
{{- if .Values.persistence.enabled -}}
{{- $capacity := include "swarm-model-server.storageBytes" . | int64 -}}
{{- $needed := add (int64 $sumBytes) (div (int64 $sumBytes) 10) | int64 -}}
{{- if lt (int64 $capacity) (int64 $needed) -}}
{{- fail (printf "persistence.size %s holds %d bytes but the weights need %d (%s plus a tenth for the file being written): the download would fail with the volume full" .Values.persistence.size (int64 $capacity) (int64 $needed) (include "swarm-model-server.humanBytes" (dict "n" $sumBytes))) -}}
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
{{- if not .Values.ingress.paths -}}
{{- fail "ingress.paths is empty and the ingress is on: the object would be created, the cloud would write its DNS, and nothing would be routed to the engine" -}}
{{- end -}}
{{- range $path := .Values.ingress.paths -}}
{{- if not (hasPrefix "/v1" ($path | toString)) -}}
{{- fail (printf "ingress.paths carries %q: vLLM authenticates /v1, /v2 and /inference and serves /metrics, /docs and /tokenize to whoever reaches them, so this chart publishes paths under /v1 only. An engine's own prefix is added to these, not written into them" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
One engine's values. Called for the single `model` block and for every entry of
`engines` alike, so a mistake is refused in the same words wherever it was
written; `engine.path` is what the message calls the place it was found.
*/}}
{{- define "swarm-model-server.validateEngine" -}}
{{- $ctx := .ctx -}}
{{- $engine := .engine -}}
{{- $multi := .multi -}}
{{- $shared := .shared -}}
{{- $m := $engine.model -}}
{{- $at := $engine.path -}}
{{- $mp := ternary (printf "%s.model" $at) $at $multi -}}

{{/* The name is a URL path segment, a container port name and a directory. The
     port name is the tightest of the three: an IANA_SVC_NAME is at most 15
     characters of lower-case alphanumerics and hyphens, with no two adjacent
     and a letter somewhere in it. */}}
{{- if $multi -}}
{{- if not $engine.name -}}
{{- fail (printf "%s.name is empty: an engine in the `engines` list is identified by name — it is the URL prefix the model is served under, the port name the Service routes to and the directory its weights land in" $at) -}}
{{- end -}}
{{- if gt (len $engine.name) 15 -}}
{{- fail (printf "%s.name %q is %d characters: it becomes a container port name, which Kubernetes limits to 15" $at $engine.name (len $engine.name)) -}}
{{- end -}}
{{- if not (regexMatch "^[a-z]([a-z0-9]|-[a-z0-9])*$" $engine.name) -}}
{{- fail (printf "%s.name %q: it becomes a container port name and a URL path segment, so it is lower-case letters, digits and single hyphens, starting with a letter" $at $engine.name) -}}
{{- end -}}
{{- if has $engine.name (list "v1" "v2" "inference" "cohere" "health" "metrics" "docs" "tokenize" "version" "ping" "openapi") -}}
{{- fail (printf "%s.name %q is one of the engine's own routes: under --root-path /%s the engine would serve its %s at /%s/%s, and the published path would read /%s/v1" $at $engine.name $engine.name $engine.name $engine.name $engine.name $engine.name) -}}
{{- end -}}
{{- end -}}

{{- if not $m.id -}}
{{- fail (printf "%s.id is empty: it is the name this endpoint advertises in /v1/models and the name a client asks for" $mp) -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9._-]*$" $m.id) -}}
{{- fail (printf "%s.id %q: a model id travels in a URL fragment and in an OpenAI request body, so it is limited to letters, digits, dot, underscore and hyphen" $mp $m.id) -}}
{{- end -}}

{{/* Both are defaulted for the single `model` block, where the card is entirely
     its own. An engine in the `engines` list cannot inherit a default: the whole
     premise of that list is that every claim on a shared card was budgeted
     deliberately, and `--max-model-len` left unset makes vLLM take the model's
     own maximum — which for these models wants a KV cache larger than the share
     the engine was given. */}}
{{- if $multi -}}
{{- if not $m.gpuMemoryUtilization -}}
{{- fail (printf "%s.gpuMemoryUtilization is unset: an engine that may share the card states its share, because a share nobody chose is a share that collides with the other engine's" $mp) -}}
{{- end -}}
{{- if or (le (float64 $m.gpuMemoryUtilization) 0.0) (gt (float64 $m.gpuMemoryUtilization) 0.95) -}}
{{- fail (printf "%s.gpuMemoryUtilization is %v: it is a fraction of the card, above 0 and at most 0.95" $mp $m.gpuMemoryUtilization) -}}
{{- end -}}
{{- if not $m.maxModelLen -}}
{{- fail (printf "%s.maxModelLen is unset: vLLM would take the model's own maximum, which wants a KV cache larger than the share this engine was budgeted, and refuse to start" $mp) -}}
{{- end -}}
{{- end -}}

{{- $w := $m.weights | default dict -}}
{{- if not $w.repo -}}
{{- fail (printf "%s.weights.repo is empty: there is nothing to fetch the weights from" $mp) -}}
{{- end -}}
{{- if not $w.revision -}}
{{- fail (printf "%s.weights.revision is empty: a branch name is not a pin, and `main` moves. Give the commit sha the manifest was built from" $mp) -}}
{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{40}$" $w.revision) -}}
{{- fail (printf "%s.weights.revision %q is not a 40-character commit sha" $mp $w.revision) -}}
{{- end -}}
{{- if not $w.files -}}
{{- fail (printf "%s.weights.files is empty: this chart will not serve weights it cannot verify. Build a manifest with scripts/model-weights-manifest.py" $mp) -}}
{{- end -}}
{{- $sum := 0 -}}
{{- range $i, $f := $w.files -}}
{{- if not $f.path -}}{{- fail (printf "%s.weights.files[%d] has no path" $mp $i) -}}{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" (toString ($f.sha256 | default ""))) -}}
{{- fail (printf "%s.weights.files[%d] (%s) has no sha256: an unverifiable file is the whole problem this manifest exists to solve" $mp $i $f.path) -}}
{{- end -}}
{{- if not $f.size -}}{{- fail (printf "%s.weights.files[%d] (%s) has no size" $mp $i $f.path) -}}{{- end -}}
{{- $sum = add $sum (int64 $f.size) -}}
{{- end -}}
{{- if ne (int64 $sum) (int64 ($w.totalBytes | default 0)) -}}
{{- fail (printf "%s.weights.totalBytes is %d but the files add up to %d: the manifest was edited by hand" $mp (int64 ($w.totalBytes | default 0)) (int64 $sum)) -}}
{{- end -}}

{{- $tc := $m.toolCalling | default dict -}}
{{- if $tc.enabled -}}
{{- $parsers := include "swarm-model-server.toolParsers" $ctx | fromJsonArray -}}
{{- if not (has ($tc.parser | default "") $parsers) -}}
{{- fail (printf "%s.toolCalling.parser %q is not one of the parsers vLLM %s registers: %s. A parser that does not match the model's output format turns every tool call into prose" $mp ($tc.parser | default "") $ctx.Chart.AppVersion (join ", " $parsers)) -}}
{{- end -}}
{{- end -}}

{{/* The trap this chart was caught by once. A listing that sets a template
     inline renders here and is refused by the marketplace's publish parse, which
     is a long way from the person who wrote it — so it is refused here instead,
     where the message can say what to do. */}}
{{- if $m.chatTemplate -}}
{{- fail "model.chatTemplate is gone: a chat template is Jinja, and a listing cannot carry `{{ }}` because the marketplace parses it as its own expression language. Put the template in charts/swarm-model-server/files/chat-templates/ and name it with model.chatTemplateFile" -}}
{{- end -}}
{{- if $m.chatTemplateFile -}}
{{- $file := printf "files/chat-templates/%s" $m.chatTemplateFile -}}
{{- if not ($ctx.Files.Get $file) -}}
{{- fail (printf "%s.chatTemplateFile %q does not exist: %s is not in the chart, so the engine would start with no template at all" $mp $m.chatTemplateFile $file) -}}
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
One engine's command line. Collected in one place because what it says is the
whole behaviour of that engine, and because the arguments are rendered into the
manifests — so anything secret must not be here (marketplace trap 4).

`--root-path` is what makes several engines on one hostname work without a
rewrite in front of them: with it set, the engine both routes and *authenticates*
`/<name>/v1/…` — vLLM's AuthenticationMiddleware strips `scope["root_path"]`
before it compares the path against its guarded prefixes
(`vllm/entrypoints/openai/server_utils.py`), and Starlette strips it again before
matching a route. Measured in charts/tests/root_path_guard.py, because an engine
that routed the prefix without guarding it would publish an unauthenticated
inference endpoint, and nothing rendered would say so.
*/}}
{{- define "swarm-model-server.engineArgs" -}}
{{- $ctx := .ctx -}}
{{- $engine := .engine -}}
{{- $m := $engine.model -}}
{{- $args := list "--model" $engine.dir "--served-model-name" $m.id -}}
{{- $args = concat $args (list "--host" "0.0.0.0" "--port" ($engine.port | toString)) -}}
{{- if $engine.prefix -}}
{{- $args = concat $args (list "--root-path" $engine.prefix) -}}
{{- end -}}
{{- if $m.maxModelLen -}}
{{- $args = concat $args (list "--max-model-len" ($m.maxModelLen | toString)) -}}
{{- end -}}
{{- if $m.gpuMemoryUtilization -}}
{{- $args = concat $args (list "--gpu-memory-utilization" ($m.gpuMemoryUtilization | toString)) -}}
{{- end -}}
{{- if $m.dtype -}}
{{- $args = concat $args (list "--dtype" ($m.dtype | toString)) -}}
{{- end -}}
{{- if $m.quantization -}}
{{- $args = concat $args (list "--quantization" ($m.quantization | toString)) -}}
{{- end -}}
{{- if gt (int $ctx.Values.gpu.count) 1 -}}
{{- $args = concat $args (list "--tensor-parallel-size" ($ctx.Values.gpu.count | toString)) -}}
{{- end -}}
{{- $tc := $m.toolCalling | default dict -}}
{{- if $tc.enabled -}}
{{- $args = concat $args (list "--enable-auto-tool-choice" "--tool-call-parser" $tc.parser) -}}
{{- end -}}
{{- if $engine.chatTemplatePath -}}
{{- $args = concat $args (list "--chat-template" $engine.chatTemplatePath) -}}
{{- end -}}
{{- $args = concat $args ($m.extraArgs | default list) -}}
{{- toJson $args -}}
{{- end -}}

{{/*
What `files/run-engines.py` is handed: every engine's name, port and full command
line, in the ConfigMap the cloud digests. The supervisor chooses nothing — a
deployment's two engines are as attested as a deployment's one.

`vllm serve` is the published image's own ENTRYPOINT, so a supervised engine is
started exactly as the single-engine container starts its one.
*/}}
{{- define "swarm-model-server.enginesJson" -}}
{{- $ctx := .ctx -}}
{{- $out := list -}}
{{- range $engine := .engines -}}
{{- $out = append $out (dict
      "name" $engine.name
      "model" $engine.model.id
      "port" (int $engine.port)
      "rootPath" $engine.prefix
      "command" (concat (list "vllm" "serve") (include "swarm-model-server.engineArgs" (dict "ctx" $ctx "engine" $engine) | fromJsonArray))
      "env" (dict
        "VLLM_CACHE_ROOT" (printf "%s/vllm" $engine.cacheRoot)
        "TRITON_CACHE_DIR" (printf "%s/triton" $engine.cacheRoot)
        "TORCHINDUCTOR_CACHE_DIR" (printf "%s/inductor" $engine.cacheRoot)
      )
   ) -}}
{{- end -}}
{{- toPrettyJson (dict "engines" $out) -}}
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
