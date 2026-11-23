{{- define "ccd.name" -}}
{{- required "confidential-clinical-documentation: name is required" .Values.name -}}
{{- end -}}

{{- define "ccd.labels" -}}
app.kubernetes.io/name: {{ include "ccd.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ccd.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ccd.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ccd.imageRef" -}}
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
Refuses the configurations that render cleanly and then fail in the cluster — or, worse, work
while being something other than what the listing says they are.

Four are worth reading. A console with no password is a clinical archive on a public hostname.
A scribe bound to anything but loopback is an unauthenticated consultation API reachable from
every pod in the space. A diarization floor of zero turns the reliability check into a
formality, which is this listing's whole subject. And a GPU requested by more than one container
never schedules at all: the device plugin gives the one card to exactly one container.
*/}}
{{- define "ccd.validate" -}}
{{- if not .Values.console.password }}
{{- fail "confidential-clinical-documentation: console.password is required — this deployment is served on a public hostname and the console is the whole of what stands in front of every recording and every note it holds" }}
{{- end }}
{{- if not .Values.console.sessionKey }}
{{- fail "confidential-clinical-documentation: console.sessionKey is required — it signs the session cookie, and a fixed or empty key means anybody can mint one" }}
{{- end }}
{{- if lt (len .Values.console.sessionKey) 32 }}
{{- fail "confidential-clinical-documentation: console.sessionKey is shorter than 32 characters" }}
{{- end }}
{{- if not .Values.hostname }}
{{- fail "confidential-clinical-documentation: hostname is required — it is where the console is served" }}
{{- end }}
{{- if not .Values.ingress.className }}
{{- fail "confidential-clinical-documentation: ingress.className is required — an Ingress without one is created, registered, has DNS written for it, and is served by nothing" }}
{{- end }}
{{- if not (eq .Values.scribe.listenAddress "127.0.0.1") }}
{{- fail "confidential-clinical-documentation: scribe.listenAddress must be 127.0.0.1 — the scribe has no authentication of its own, and the console's session check is only the whole of what stands in front of it while the scribe listens on loopback and nothing else" }}
{{- end }}
{{- if not .Values.inference.model }}
{{- fail "confidential-clinical-documentation: inference.model is required — it is the name the generation endpoint advertises, and a wrong one is a 404 on every note this deployment tries to write" }}
{{- end }}
{{- if not .Values.inference.url }}
{{- fail "confidential-clinical-documentation: inference.url is required" }}
{{- end }}
{{- if not (hasSuffix "/v1" .Values.inference.url) }}
{{- fail "confidential-clinical-documentation: inference.url must end in /v1 — the OpenAI client appends /chat/completions to it" }}
{{- end }}
{{- /*
The diarization floors. Upstream has none, and a note generated from a transcript with no
speaker separation is fluent, correctly structured and attributes the patient's symptoms to the
clinician. A floor of zero is that behaviour with a reassuring field name.
*/ -}}
{{- if .Values.scribe.diarization.enabled }}
{{- range $floor, $value := .Values.scribe.diarization.floors }}
{{- if le ($value | float64) 0.0 }}
{{- fail (printf "confidential-clinical-documentation: scribe.diarization.floors.%s must be above zero — a floor of zero means every diarization result passes, including one that put both people in one cluster, and the note is then written from a confident wrong attribution nothing reports" $floor) }}
{{- end }}
{{- if gt ($value | float64) 1.0 }}
{{- fail (printf "confidential-clinical-documentation: scribe.diarization.floors.%s is a fraction and cannot exceed 1.0" $floor) }}
{{- end }}
{{- end }}
{{- if or (le (.Values.scribe.diarization.threshold | float64) 0.0) (ge (.Values.scribe.diarization.threshold | float64) 1.0) }}
{{- fail "confidential-clinical-documentation: scribe.diarization.threshold is an embedding-distance threshold between 0 and 1, not a speaker count" }}
{{- end }}
{{- end }}
{{- /*
The card. One H100 reaches exactly one container, so the generation engine holds it and
recognition and diarization run on the CPU. A GPU request on this pod is not a tuning mistake
that costs throughput — it is a Pod that stays Pending for ever beside an engine that cannot
start either.
*/ -}}
{{- range $container := list "console" "scribe" "egress" }}
{{- $resources := index $.Values $container "resources" }}
{{- range $kind := list "requests" "limits" }}
{{- $gpu := index $resources $kind "nvidia.com/gpu" }}
{{- if ne ($gpu | toString) "0" }}
{{- fail (printf "confidential-clinical-documentation: %s.resources.%s.nvidia.com/gpu must be \"0\" — the device plugin gives the one card to exactly one container, and that container is the generation engine. This pod does recognition and diarization on the CPU by design" $container $kind) }}
{{- end }}
{{- end }}
{{- end }}
{{- if .Values.egress.enabled }}
{{- if .Values.egress.allowHost }}
{{- fail "confidential-clinical-documentation: egress.allowHost must be empty — this deployment reaches nothing. Its models are inside its image, its generation endpoint is a Service in this namespace, and its transcript corpus is reached through the gatekeeper at a local address. An allowlist with an entry in it is a claim this listing does not make" }}
{{- end }}
{{- if not (eq .Values.egress.listenAddress "127.0.0.1") }}
{{- fail "confidential-clinical-documentation: egress.listenAddress must be 127.0.0.1 — a proxy on a pod IP is an egress path for the whole cluster space, which is the opposite of what this container is for" }}
{{- end }}
{{- end }}
{{- $bodyLimitMiB := (regexReplaceAll "[^0-9]" .Values.ingress.maxBodySize "" | int) }}
{{- if and (hasSuffix "m" .Values.ingress.maxBodySize) (lt $bodyLimitMiB (div (.Values.scribe.maxFileSizeBytes | int) 1048576)) }}
{{- fail (printf "confidential-clinical-documentation: ingress.maxBodySize (%s) is below scribe.maxFileSizeBytes (%d) — the ingress would cut the upload off with a 413 the console never sees, so the error a clinician reads would come from nginx rather than from the deployment" .Values.ingress.maxBodySize (.Values.scribe.maxFileSizeBytes | int)) }}
{{- end }}
{{- end -}}

{{/*
A liveness/readiness probe for a process listening on loopback, written in python because this
image carries no curl and no wget. `--noproxy` has no analogue here, so the proxy environment is
cleared explicitly: HTTP_PROXY is set on the scribe container and urllib honours it, which would
send the probe to the egress guard — which admits nothing and would therefore fail every probe
on a perfectly healthy process.
*/}}
{{- define "ccd.httpProbe" -}}
{{- printf "import os,urllib.request;[os.environ.pop(k,None) for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy')];urllib.request.urlopen('http://127.0.0.1:%d%s',timeout=%d)" (.port | int) .path (.timeout | int) -}}
{{- end -}}
