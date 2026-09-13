#!/usr/bin/env bash
#
# Installs the confidential-s3 chart into a throwaway kind cluster and asks the
# deployment for the things it exists to do: come up, let its administrator in,
# and store and return an object through the S3 endpoint its Ingress publishes.
#
#   charts/tests/smoke/confidential-s3.sh
#   KEEP=1 charts/tests/smoke/confidential-s3.sh      # leave the cluster up
#
# Needs: kind, kubectl, helm, docker, curl — and the three confidential-s3
# images. Those are a package of the Super-Protocol org and this script has no
# pull secret, so it builds them from a checkout instead:
#
#   CONFIDENTIAL_S3=~/src/confidential-s3 charts/tests/smoke/confidential-s3.sh
#
# Point GATEWAY_IMAGE / API_IMAGE / CONSOLE_IMAGE at images you already have to
# skip the build.
#
# Not in CI, for the same reason the router's smoke is not: it wants a kind
# cluster, an ingress controller and a build of another repository's images. The
# golden tests in charts/tests/run.sh are what runs on every pull request.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
cd "$root"

CLUSTER=${CLUSTER:-confidential-s3-smoke}
NS=${NS:-confidential-s3}
CONSOLE_HOST=${CONSOLE_HOST:-console.confidential-s3.test}
S3_HOST=${S3_HOST:-s3.confidential-s3.test}
GATEWAY_IMAGE=${GATEWAY_IMAGE:-confidential-s3/gateway:smoke}
API_IMAGE=${API_IMAGE:-confidential-s3/api:smoke}
CONSOLE_IMAGE=${CONSOLE_IMAGE:-confidential-s3/console:smoke}
INGRESS_NGINX=${INGRESS_NGINX:-https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml}
CONFIDENTIAL_S3=${CONFIDENTIAL_S3:-}

ADMIN_EMAIL=admin@confidential-s3.test
ADMIN_PASSWORD=smoke-password-not-a-real-one
BOOTSTRAP_TOKEN=smoke-bootstrap-token-not-a-real-one-0001
MASTER_KEY_SEED=smoke-master-key-seed-not-a-real-one-0001
ENGINE_SEED=smoke-engine-seed-not-a-real-one-000001

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

console() { curl -s --resolve "$CONSOLE_HOST:80:127.0.0.1" "$@"; }
s3()      { curl -s --resolve "$S3_HOST:80:127.0.0.1" "$@"; }
field()   { node -e 'let b="";process.stdin.on("data",c=>b+=c).on("end",()=>{const v=process.argv[1].split(".").reduce((o,k)=>o?.[k],JSON.parse(b));process.stdout.write(v==null?"":String(v))})' "$1"; }

cleanup() {
  local code=$?
  if [ $code -ne 0 ]; then
    printf '\n--- pods ---\n' >&2
    kubectl get pods -n "$NS" >&2 || true
    kubectl describe pods -n "$NS" 2>&1 | grep -A5 -E 'Warning|Error' | head -60 >&2 || true
    for d in gateway api console; do
      printf '\n--- %s ---\n' "$d" >&2
      kubectl logs -n "$NS" "deploy/confidential-s3-$d" --tail=40 >&2 || true
    done
    kubectl logs -n "$NS" job/confidential-s3-bootstrap --tail=40 >&2 || true
    kubectl logs -n "$NS" statefulset/confidential-s3-garage --tail=40 >&2 || true
  fi
  if [ -z "${KEEP:-}" ]; then
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  else
    printf '\nCluster %s left up. Delete it with: kind delete cluster --name %s\n' "$CLUSTER" "$CLUSTER"
  fi
}
trap cleanup EXIT

if [ -n "$CONFIDENTIAL_S3" ]; then
  step "Building the images from $CONFIDENTIAL_S3"
  docker build -f "$CONFIDENTIAL_S3/gateway.dockerfile" -t "$GATEWAY_IMAGE" "$CONFIDENTIAL_S3"
  docker build -f "$CONFIDENTIAL_S3/api.dockerfile" -t "$API_IMAGE" "$CONFIDENTIAL_S3"
  docker build -f "$CONFIDENTIAL_S3/console.dockerfile" -t "$CONSOLE_IMAGE" "$CONFIDENTIAL_S3"
  ok "built"
fi
for image in "$GATEWAY_IMAGE" "$API_IMAGE" "$CONSOLE_IMAGE"; do
  docker image inspect "$image" >/dev/null 2>&1 \
    || die "$image is not present; set CONFIDENTIAL_S3 to build it"
done

step "Cluster"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --config "$here/kind.yaml" --wait 180s >/dev/null
kubectl apply -f "$INGRESS_NGINX" >/dev/null
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=300s >/dev/null
kind load docker-image "$GATEWAY_IMAGE" "$API_IMAGE" "$CONSOLE_IMAGE" --name "$CLUSTER" >/dev/null
kubectl create namespace "$NS" >/dev/null
ok "kind + ingress-nginx up, images loaded"

step "Install"
helm dependency build charts/confidential-s3 >/dev/null
# Split the reference the way the chart's values do, because that is what the
# listing does too: a repository and a tag, never a floating name.
split_image() { printf '%s' "${1%:*}"; }
split_tag()   { printf '%s' "${1##*:}"; }
helm install confidential-s3 charts/confidential-s3 --namespace "$NS" \
  --set consoleHostname="$CONSOLE_HOST" \
  --set s3Hostname="$S3_HOST" \
  --set publicScheme=http \
  --set masterKey.seed="$MASTER_KEY_SEED" \
  --set engine.seed="$ENGINE_SEED" \
  --set bootstrapToken.value="$BOOTSTRAP_TOKEN" \
  --set database.password=smoke-database-password \
  --set api.bootstrap.adminEmail="$ADMIN_EMAIL" \
  --set gateway.image.registry=docker.io --set gateway.image.repository="$(split_image "$GATEWAY_IMAGE")" --set gateway.image.tag="$(split_tag "$GATEWAY_IMAGE")" --set gateway.image.pullPolicy=Never \
  --set api.image.registry=docker.io --set api.image.repository="$(split_image "$API_IMAGE")" --set api.image.tag="$(split_tag "$API_IMAGE")" --set api.image.pullPolicy=Never \
  --set console.image.registry=docker.io --set console.image.repository="$(split_image "$CONSOLE_IMAGE")" --set console.image.tag="$(split_tag "$CONSOLE_IMAGE")" --set console.image.pullPolicy=Never \
  --set garage.persistence.data.size=2Gi \
  --wait --timeout 10m >/dev/null
ok "installed"

step "Every workload is ready"
kubectl wait --namespace "$NS" --for=condition=available --timeout=300s \
  deployment/confidential-s3-gateway deployment/confidential-s3-api deployment/confidential-s3-console >/dev/null
ok "gateway, control plane and console are available"
kubectl wait --namespace "$NS" --for=condition=complete --timeout=300s \
  job/confidential-s3-bootstrap >/dev/null
ok "the engine was bootstrapped"

step "The console is served through its own Ingress"
console -fsS "http://$CONSOLE_HOST/login" | grep -qi '<html' || die 'the console did not serve a sign-in page'
ok "GET http://$CONSOLE_HOST/login"

step "The control plane is not published"
kubectl get ingress -n "$NS" -o jsonpath='{.items[*].spec.rules[*].host}' \
  | tr ' ' '\n' | sort > /tmp/cs3-smoke-hosts.$$
printf '%s\n%s\n' "$CONSOLE_HOST" "$S3_HOST" | sort | diff -q - /tmp/cs3-smoke-hosts.$$ >/dev/null \
  || die "published hostnames are [$(tr '\n' ' ' < /tmp/cs3-smoke-hosts.$$)], not just the console and the S3 endpoint"
rm -f /tmp/cs3-smoke-hosts.$$
ok "only the console and the S3 endpoint have a hostname"

step "First sign-in, and a bucket"
# The console is a back-end-for-front-end, so the browser's own calls go to it;
# the control plane is reached here the same way the console reaches it.
api() {
  local method=$1 path=$2 body=${3:-}
  kubectl run -n "$NS" "cs3-smoke-$RANDOM" --rm -i --restart=Never --quiet \
    --image=curlimages/curl:8.11.1 --command -- \
    curl -fsS -X "$method" "http://confidential-s3-api:3000/api$path" \
    ${TOKEN:+-H "authorization: Bearer $TOKEN"} \
    ${body:+-H 'content-type: application/json' -d "$body"}
}
TOKEN=''
TOKEN=$(api POST /auth/redeem \
  "{\"email\":\"$ADMIN_EMAIL\",\"token\":\"$BOOTSTRAP_TOKEN\",\"password\":\"$ADMIN_PASSWORD\"}" | field accessToken)
[ -n "$TOKEN" ] || die 'redeeming the bootstrap token returned no session'
ok "the bootstrap token was redeemed for $ADMIN_EMAIL"

BUCKET_ID=$(api POST /buckets '{"name":"smoke","mode":"internal"}' | field id)
[ -n "$BUCKET_ID" ] || die 'creating an internal bucket returned no id'
ok "internal bucket smoke"

CREDENTIALS=$(api POST /service-accounts '{"name":"smoke"}')
ACCOUNT_ID=$(printf '%s' "$CREDENTIALS" | field serviceAccount.id)
ACCESS_KEY=$(printf '%s' "$CREDENTIALS" | field accessKey)
SECRET_KEY=$(printf '%s' "$CREDENTIALS" | field secretKey)
[ -n "$ACCESS_KEY" ] && [ -n "$SECRET_KEY" ] || die 'creating a service account returned no credential'
api PUT "/service-accounts/$ACCOUNT_ID/grants/$BUCKET_ID" '{"actions":["read","write","list","delete"]}' >/dev/null
ok "a service account, granted on the bucket"

step "An object, through the published S3 endpoint"
# Through the Ingress, with the hostname resolved locally — the same path a
# client outside the cluster takes, annotations and all. Not a port-forward,
# which would prove the pods work and leave the routing untried.
ROUND_TRIP=$(docker run --rm --network host \
  -e AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
  -e AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e AWS_EC2_METADATA_DISABLED=true \
  --add-host "$S3_HOST:127.0.0.1" \
  --entrypoint /bin/sh amazon/aws-cli:2.22.0 -c "
    set -e
    printf 'confidential-s3 smoke\n' > /tmp/object.txt
    aws --endpoint-url http://$S3_HOST s3api put-object --bucket smoke --key smoke/object.txt --body /tmp/object.txt > /dev/null
    aws --endpoint-url http://$S3_HOST s3api get-object --bucket smoke --key smoke/object.txt /tmp/read-back.txt > /dev/null
    cat /tmp/read-back.txt
  ")
[ "$ROUND_TRIP" = 'confidential-s3 smoke' ] || die "the object did not come back unchanged: [$ROUND_TRIP]"
ok "put, get, byte-for-byte"

UNSIGNED=$(s3 -o /dev/null -w '%{http_code}' "http://$S3_HOST/smoke/smoke/object.txt")
[ "$UNSIGNED" = '403' ] || die "an unsigned GET answered $UNSIGNED, not 403"
ok "an unsigned request is refused"

step "The bootstrap Job is safe to run twice"
# The platform deletes and re-creates it on every reconfigure, so this is not a
# hypothetical: a Job that failed the second time would take a reconfigure with
# it.
kubectl delete job -n "$NS" confidential-s3-bootstrap >/dev/null
helm upgrade confidential-s3 charts/confidential-s3 --namespace "$NS" --reuse-values --wait --timeout 5m >/dev/null
kubectl wait --namespace "$NS" --for=condition=complete --timeout=300s job/confidential-s3-bootstrap >/dev/null
ok "it completed against an engine that was already bootstrapped"

printf '\n\033[1meverything passed\033[0m\n'
