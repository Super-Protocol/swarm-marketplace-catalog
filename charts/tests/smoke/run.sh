#!/usr/bin/env bash
#
# Installs the three confidential-router charts into a throwaway kind cluster,
# alongside the ollama and postgresql they depend on, and asks the deployment for
# the things it exists to do: sign a user in, mint a key, list the models that
# were switched on, and answer a generation through LiteLLM and Ollama.
#
#   charts/tests/smoke/run.sh
#   KEEP=1 charts/tests/smoke/run.sh      # leave the cluster up afterwards
#
# Needs: kind, kubectl, helm, docker, curl, jq — and the two router images.
# Those are a private package of the Super-Protocol org, so this builds them from
# a checkout of Super-Protocol/confidential-router instead of pulling:
#
#   CONFIDENTIAL_ROUTER=~/src/confidential-router charts/tests/smoke/run.sh
#
# Point ROUTER_API_IMAGE / ROUTER_UI_IMAGE at images you already have to skip the
# build. Any console image will do: it is told its API origin at run time.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
cd "$root"

CLUSTER=${CLUSTER:-confidential-router-smoke}
NS=${NS:-confidential-router}
API_HOST=${API_HOST:-api.confidential-router.test}
CONSOLE_HOST=${CONSOLE_HOST:-console.confidential-router.test}
ROUTER_API_IMAGE=${ROUTER_API_IMAGE:-confidential-router/router-api:smoke}
ROUTER_UI_IMAGE=${ROUTER_UI_IMAGE:-confidential-router/router-ui:smoke}
INGRESS_NGINX=${INGRESS_NGINX:-https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml}
CONFIDENTIAL_ROUTER=${CONFIDENTIAL_ROUTER:-}

# One model, so the run does not spend ten minutes downloading weights. The
# assertions compare against what the chart renders rather than a hard-coded
# list. `OLLAMA_MODEL` is the one name all three charts are given.
OLLAMA_MODEL=llama3.2:3b
ROUTER_MODEL_ID=meta/llama-3.2-3b-instruct:tee

api() { curl -s --resolve "$API_HOST:80:127.0.0.1" "$@"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

cleanup() {
  local code=$?
  if [ $code -ne 0 ]; then
    printf '\n--- pods ---\n' >&2
    kubectl get pods -n "$NS" >&2 || true
    kubectl logs -n "$NS" deploy/confidential-router-api --tail=40 >&2 || true
  fi
  if [ -z "${KEEP:-}" ]; then
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  else
    printf '\nCluster %s left up. Delete it with: kind delete cluster --name %s\n' "$CLUSTER" "$CLUSTER"
  fi
}
trap cleanup EXIT

if [ -n "$CONFIDENTIAL_ROUTER" ]; then
  step "Building the router images from $CONFIDENTIAL_ROUTER"
  docker build -f "$CONFIDENTIAL_ROUTER/router-api.dockerfile" -t "$ROUTER_API_IMAGE" "$CONFIDENTIAL_ROUTER"
  docker build -f "$CONFIDENTIAL_ROUTER/router-ui.dockerfile" -t "$ROUTER_UI_IMAGE" "$CONFIDENTIAL_ROUTER"
  ok "built"
fi
docker image inspect "$ROUTER_API_IMAGE" >/dev/null 2>&1 || die "$ROUTER_API_IMAGE is not present; set CONFIDENTIAL_ROUTER to build it"
docker image inspect "$ROUTER_UI_IMAGE" >/dev/null 2>&1 || die "$ROUTER_UI_IMAGE is not present; set CONFIDENTIAL_ROUTER to build it"

step "Cluster"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --config "$here/kind.yaml" --wait 180s >/dev/null
kubectl apply -f "$INGRESS_NGINX" >/dev/null
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=300s >/dev/null
kind load docker-image "$ROUTER_API_IMAGE" "$ROUTER_UI_IMAGE" --name "$CLUSTER" >/dev/null
kubectl create namespace "$NS" >/dev/null
ok "kind + ingress-nginx up, router images loaded"

step "Install"
helm repo add otwld https://otwld.github.io/ollama-helm/ >/dev/null 2>&1 || true
helm repo update otwld >/dev/null
helm upgrade --install ollama otwld/ollama --version 1.12.0 -n "$NS" \
  -f "$here/ollama.yaml" --set "ollama.models.pull[0]=$OLLAMA_MODEL" >/dev/null
helm dependency build charts/confidential-router-api >/dev/null
for release in litellm api ui; do
  case $release in
    litellm) chart=charts/confidential-router-litellm; name=confidential-router-litellm; extra=() ;;
    api) chart=charts/confidential-router-api; name=confidential-router-api
         extra=(--set "image.repository=${ROUTER_API_IMAGE%%:*}" --set "image.tag=${ROUTER_API_IMAGE##*:}") ;;
    ui) chart=charts/confidential-router-ui; name=confidential-router-ui
        extra=(--set "image.repository=${ROUTER_UI_IMAGE%%:*}" --set "image.tag=${ROUTER_UI_IMAGE##*:}") ;;
  esac
  helm upgrade --install "$name" "$chart" -n "$NS" -f "$here/$release.yaml" \
    --set "apiHostname=$API_HOST" --set "consoleHostname=$CONSOLE_HOST" \
    --set "models[0]=$OLLAMA_MODEL" "${extra[@]}" >/dev/null
done
ok "four releases installed"

step "Everything becomes ready"
for deploy in ollama confidential-router-litellm confidential-router-api confidential-router-ui; do
  kubectl rollout status "deploy/$deploy" -n "$NS" --timeout=600s >/dev/null || die "$deploy never became ready"
  ok "$deploy"
done
kubectl rollout status statefulset/confidential-router-postgresql -n "$NS" --timeout=300s >/dev/null
ok "confidential-router-postgresql"

step "The API is healthy against PostgreSQL"
health=$(kubectl exec -n "$NS" deploy/confidential-router-api -c router-api -- \
  node -e "fetch('http://127.0.0.1:3000/health').then(r=>r.text()).then(t=>console.log(t))")
printf '%s' "$health" | jq -e '.status == "ok" and .database.status == "up"' >/dev/null \
  || die "/health said: $health"
ok "$health"

step "The ingresses route"
[ "$(curl -s -o /dev/null -w '%{http_code}' --resolve "$CONSOLE_HOST:80:127.0.0.1" "http://$CONSOLE_HOST/login")" = 200 ] \
  || die "the console did not serve /login"
ok "console /login"
# `/health` is deliberately not published; a 404 here is the ingress path list
# doing its job.
[ "$(api -o /dev/null -w '%{http_code}' "http://$API_HOST/health")" = 404 ] \
  || die "/health is reachable from outside the cluster"
ok "api /health is not published"

step "The catalogue is the one the list asked for"
expected=$(helm template x charts/confidential-router-api -f "$here/api.yaml" \
  --set "apiHostname=$API_HOST" --set "consoleHostname=$CONSOLE_HOST" --set "models[0]=$OLLAMA_MODEL" \
  --set "image.repository=${ROUTER_API_IMAGE%%:*}" --set "image.tag=${ROUTER_API_IMAGE##*:}" \
  --show-only templates/configmap.yaml \
  | grep -o '^ *- id: "[^"]*"' | sed 's/.*id: "//; s/"$//' | sort)
[ -n "$expected" ] || die "the chart rendered no models"
graphql_models=$(api -X POST "http://$API_HOST/graphql" -H 'content-type: application/json' \
  -d '{"query":"{ models { id } }"}' | jq -r '.data.models[].id' | sort)
[ "$graphql_models" = "$expected" ] || die "GraphQL listed [$graphql_models], the chart rendered [$expected]"
ok "GraphQL: $(printf '%s' "$graphql_models" | tr '\n' ' ')"

step "A console session, a top-up and a key — the way the console gets them"
email="smoke-$(date +%s)@confidential-router.test"
api -X POST "http://$API_HOST/auth/sign-in/magic-link" -H 'content-type: application/json' \
  -H "origin: http://$CONSOLE_HOST" -d "{\"email\":\"$email\",\"callbackURL\":\"/\"}" | jq -e '.status == true' >/dev/null \
  || die "the magic-link request was refused"
link=""
for _ in $(seq 1 20); do
  link=$(kubectl logs -n "$NS" deploy/confidential-router-api -c router-api --tail=400 2>/dev/null \
    | grep -o "Magic link for $email: [^\"\\]*" | tail -1 | sed 's/.*: //')
  [ -n "$link" ] && break
  sleep 2
done
[ -n "$link" ] || die "no magic link was written to the log"
cookie=$(api "$link" -o /dev/null -D - | grep -i '^set-cookie:' | sed 's/^[Ss]et-[Cc]ookie: //' | cut -d';' -f1 | paste -sd'; ')
[ -n "$cookie" ] || die "the magic link set no session cookie"
gql() { api -X POST "http://$API_HOST/graphql" -H 'content-type: application/json' \
  -H "origin: http://$CONSOLE_HOST" -H "cookie: $cookie" -d "$1"; }
workspace=$(gql '{"query":"query Me { me { workspaces { id } } }"}' | jq -r '.data.me.workspaces[0].id')
[ "$workspace" != null ] || die "the signed-in user has no workspace"
ok "signed in as $email"

checkout=$(gql "{\"query\":\"mutation T(\$i: CreateCheckoutInput!){ createCheckout(input:\$i){ url } }\",\"variables\":{\"i\":{\"workspaceId\":\"$workspace\",\"amountMicros\":\"10000000\"}}}" | jq -r '.data.createCheckout.url')
api -o /dev/null "$checkout"
balance=$(gql "{\"query\":\"query B(\$w: ID!){ creditBalance(workspaceId:\$w){ balanceMicros } }\",\"variables\":{\"w\":\"$workspace\"}}" | jq -r '.data.creditBalance.balanceMicros')
[ "$balance" = 10000000 ] || die "the manual top-up left a balance of $balance"
ok "topped up to \$$((balance / 1000000))"

key=$(gql "{\"query\":\"mutation K(\$i: CreateApiKeyInput!){ createApiKey(input:\$i){ secret } }\",\"variables\":{\"i\":{\"workspaceId\":\"$workspace\",\"name\":\"smoke\"}}}" | jq -r '.data.createApiKey.secret')
case "$key" in sk-tee-v1-*) ok "minted ${key:0:14}…" ;; *) die "createApiKey returned $key" ;; esac

step "/v1/models lists exactly the models that were selected"
v1_models=$(api "http://$API_HOST/v1/models" -H "authorization: Bearer $key" | jq -r '.data[].id' | sort)
[ "$v1_models" = "$expected" ] || die "/v1/models listed [$v1_models], the chart rendered [$expected]"
ok "$(printf '%s' "$v1_models" | tr '\n' ' ')"

# Asked from inside the namespace, through the router's own pod: the proxy is
# ClusterIP and there is nothing to port-forward to on purpose.
litellm_models=$(kubectl exec -n "$NS" deploy/confidential-router-api -c router-api -- \
  node -e "fetch('http://confidential-router-litellm:4000/v1/models').then(r=>r.text()).then(t=>console.log(t))" \
  | jq -r '.data[].id' | sort)
[ -n "$litellm_models" ] || die "LiteLLM served no catalogue"
ok "litellm: $(printf '%s' "$litellm_models" | tr '\n' ' ')"

step "A generation, end to end"
# Ollama has just started and the weights are on disk, not in memory: this first
# request pays the load, which is the case the router's connect timeout has to be
# sized for.
answer=$(api "http://$API_HOST/v1/chat/completions" -H "authorization: Bearer $key" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"$ROUTER_MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly the word: pong\"}],\"max_tokens\":16,\"temperature\":0}")
printf '%s' "$answer" | jq -e '.choices[0].message.content | length > 0' >/dev/null \
  || die "the generation failed: $answer"
printf '%s' "$answer" | jq -e '.usage.cost_micros > 0' >/dev/null \
  || die "the generation was not metered: $answer"
ok "answered $(printf '%s' "$answer" | jq -r '.choices[0].message.content' | head -c 40 | tr '\n' ' ')— $(printf '%s' "$answer" | jq -r '.usage.cost_micros') micro-USD"

step "Streaming arrives as it is produced"
headers=$(mktemp)
chunks=$(api -N "http://$API_HOST/v1/chat/completions" -H "authorization: Bearer $key" \
  -H 'content-type: application/json' -D "$headers" \
  -d "{\"model\":\"$ROUTER_MODEL_ID\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Count to five\"}],\"max_tokens\":40,\"temperature\":0}" \
  | grep -c '^data: ')
grep -qi 'content-type: text/event-stream' "$headers" || die "the stream was not served as text/event-stream"
[ "$chunks" -gt 2 ] || die "the stream arrived as $chunks chunk(s); the ingress is buffering"
rm -f "$headers"
ok "$chunks SSE chunks through the ingress"

printf '\n\033[1msmoke passed\033[0m\n\n'
