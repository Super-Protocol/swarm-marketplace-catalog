{{/*
An image reference, by digest where one is declared.

Same rule and same helper shape as charts/swarm-model-server: a tag names a release and a
digest names the bytes, and only one of those is what the deployment attests to. The tag is
kept in values beside the digest so a reader does not have to resolve a hash to find out
which version this is.
*/}}
{{- define "analyst-embeddings.imageRef" -}}
{{- $repo := printf "%s/%s" .registry .repository -}}
{{- if .digest -}}
{{- printf "%s@%s" $repo .digest -}}
{{- else if .tag -}}
{{- printf "%s:%s" $repo .tag -}}
{{- else -}}
{{- fail "neither digest nor tag is set: every image in this chart is pinned" -}}
{{- end -}}
{{- end -}}
