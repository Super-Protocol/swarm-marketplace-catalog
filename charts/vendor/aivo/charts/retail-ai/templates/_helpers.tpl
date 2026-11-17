{{/*
Expand the name of the chart.
*/}}
{{- define "app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "app.labelsCommon" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabelsCommon" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "app.selectorLabelsCommon" -}}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Back labels
*/}}
{{- define "app.labelsBack" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabelsBack" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "app.selectorLabelsBack" -}}
app.kubernetes.io/name: {{ include "app.name" . }}-back
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Worker labels — the api pods that drain the background queues (AIV-1265). Their
own selector on purpose: the backend Service must not route user traffic to a
process that serves no api.
*/}}
{{- define "app.labelsWorker" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabelsWorker" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "app.selectorLabelsWorker" -}}
app.kubernetes.io/name: {{ include "app.name" . }}-worker
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Front labels
*/}}
{{- define "app.labelsFront" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabelsFront" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "app.selectorLabelsFront" -}}
app.kubernetes.io/name: {{ include "app.name" . }}-front
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount the backend pod runs as, resolved once so the deployment and the
licensing ClusterRoleBinding can never name different accounts (AIV-894).

- an explicit back.serviceAccountName wins, and the operator owns that account:
  the chart neither creates it nor assumes anything about it;
- otherwise, when the licensing cluster-UID lookup is enabled, the account this
  chart creates;
- otherwise empty, which leaves the pod on the namespace default.
*/}}
{{- define "app.backServiceAccountName" -}}
{{- if .Values.back.serviceAccountName -}}
{{- .Values.back.serviceAccountName -}}
{{- else if .Values.licensing.clusterUid.enabled -}}
{{- printf "%s-back" (include "app.fullname" .) -}}
{{- end -}}
{{- end }}

{{/* --- HA placement: begin (AIV-2368, AIV-2669) ------------------------------
This block is the same, byte for byte, in every platform subchart — config,
service-discovery, identity, instance and retail-ai. Template names are global
across a parent chart and its subcharts, so two different bodies under one name
would make one chart silently render the other's; tests/platform-ha.sh keeps
the copies identical.
*/}}

{{/*
Default anti-affinity of a role's pods (AIV-2368): keep two replicas of the SAME
role off one node.

`preferred` by default, on purpose. The constraint is about surviving the loss
of a node, not about capacity: a cluster with fewer schedulable nodes than
replicas — a single-node stand, a cluster mid-drain — must still place the pod
rather than leave it Pending, and a required term would do exactly that.

`required` (AIV-2669) is for a cluster sized for it — at least as many worker
nodes as replicas, plus one for a rolling update's surge pod. There the soft
term is not enough: after a node comes back the scheduler is free to keep both
replicas on the survivor, and the next failure of that node takes the role down
whole. The price is a Pending pod whenever the nodes run out — a drain of one
of two nodes, or `maxSurge: 1` on a cluster with exactly `replicas` nodes.

Takes the role's selector labels as a rendered string: a term matching
`app.kubernetes.io/name: <release>-back` spreads the backend against the backend
and says nothing about the worker, which is the point — the two Deployments
compete for CPU, so they are allowed to share a node when they must.
*/}}
{{- define "app.podAntiAffinity" -}}
podAntiAffinity:
{{- if .required }}
  requiredDuringSchedulingIgnoredDuringExecution:
  - topologyKey: kubernetes.io/hostname
    labelSelector:
      matchLabels:
{{ .selector | indent 8 }}
{{- else }}
  preferredDuringSchedulingIgnoredDuringExecution:
  - weight: 100
    podAffinityTerm:
      topologyKey: kubernetes.io/hostname
      labelSelector:
        matchLabels:
{{ .selector | indent 10 }}
{{- end }}
{{- end }}

{{/*
The `affinity` a role's pod is rendered with: the default anti-affinity above
with the installation's own `affinity` layered over it.

Layered rather than either/or, because the two keys answer different questions
and an installation that sets one almost never means to drop the other: the
customer-side values that exist today pin pods to a node pool
(`nodeAffinity`, see tests/retail-ai-worker.sh), and before this they were the
only way to write the key at all. An `affinity` that DOES carry
`podAntiAffinity` replaces the default wholesale — that is how an installation
asks for another topology key, or none.

Arguments: `explicit` — the values map (may be empty), `podAntiAffinity` — the
role's `podAntiAffinity` values (`enabled`, `required`), `selector` — the
role's selector labels.
*/}}
{{- define "app.roleAffinity" -}}
{{- $explicit := .explicit | default dict -}}
{{- $anti := .podAntiAffinity | default dict -}}
{{- $merged := $explicit -}}
{{- if and $anti.enabled (not (hasKey $explicit "podAntiAffinity")) -}}
{{- $default := include "app.podAntiAffinity" (dict "selector" .selector "required" $anti.required) | fromYaml -}}
{{- $merged = mergeOverwrite $default $explicit -}}
{{- end -}}
{{- with $merged -}}
{{- toYaml . -}}
{{- end -}}
{{- end }}

{{/*
Whether a role gets a PodDisruptionBudget (AIV-2368).

Three states, because the right answer depends on a number the installation
owns: `true` / `false` decide it outright, anything else (the shipped `auto`)
turns the budget on exactly when more than one replica is asked for. A budget
over a single replica is not caution, it is a blocked node drain — `minAvailable:
1` with one pod means the eviction API refuses every voluntary eviction forever,
and an upgrade or a node replacement hangs on it.

Arguments: `pdb` — the role's podDisruptionBudget values, `replicas` — the
replica count to judge `auto` by. Returns a non-empty string for yes.
*/}}
{{- define "app.pdbEnabled" -}}
{{- if kindIs "bool" .pdb.enabled -}}
{{- if .pdb.enabled -}}yes{{- end -}}
{{- else if gt (int .replicas) 1 -}}
yes
{{- end -}}
{{- end }}

{{/*
The `spec` of a role's PodDisruptionBudget, without the selector.

`maxUnavailable` wins when set; otherwise `minAvailable`. A spec with neither is
rejected by the apiserver, so the chart refuses to render rather than hand it
over — the failure belongs to `helm template`, not to an ArgoCD sync.

Arguments: `pdb` — the role's podDisruptionBudget values, `key` — the values
path to name in the error.
*/}}
{{- define "app.pdbSpec" -}}
{{- if .pdb.maxUnavailable -}}
maxUnavailable: {{ .pdb.maxUnavailable }}
{{- else if .pdb.minAvailable -}}
minAvailable: {{ .pdb.minAvailable }}
{{- else -}}
{{- fail (printf "%s needs minAvailable or maxUnavailable; a spec with neither is rejected by the apiserver. Set one, or turn the budget off with %s.enabled=false." .key .key) -}}
{{- end -}}
{{- end }}
{{/* --- HA placement: end --------------------------------------------------- */}}
