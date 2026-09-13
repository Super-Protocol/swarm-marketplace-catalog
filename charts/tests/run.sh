#!/usr/bin/env bash
#
# Chart tests: lint, then render every case in cases.tsv and diff it against the
# golden of the same name.
#
#   charts/tests/run.sh            # lint + render + diff
#   UPDATE=1 charts/tests/run.sh   # rewrite the goldens after an intended change
#
# Run from anywhere; paths are resolved against the repository root.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

# One release name and namespace per listing. Neither reaches an object name in
# these charts — they are chosen so the rendered labels say which listing a
# golden belongs to, and nothing more.
release_for()   { case "$1" in confidential-s3) printf 'cs3' ;; *) printf 'cr' ;; esac; }
namespace_for() { case "$1" in confidential-s3) printf 'confidential-s3' ;; *) printf 'confidential-router' ;; esac; }

RELEASE=cr
NAMESPACE=confidential-router
CHARTS=(confidential-router-api confidential-router-litellm confidential-router-ui confidential-s3)

update="${UPDATE:-}"
failures=0

note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

# Only the documents the case's own chart produced. A vendored subchart's output
# is the vendor's contract, not this repository's, and bumping one would
# otherwise rewrite every golden that installs it.
own_documents() {
  awk -v prefix="$1/templates/" '
    BEGIN { keep = 0; block = "" }
    /^---$/ {
      if (keep && block != "") printf "---\n%s", block
      block = ""; keep = 0; next
    }
    {
      block = block $0 "\n"
      if (substr($0, 1, 10) == "# Source: ") keep = (index(substr($0, 11), prefix) == 1)
    }
    END { if (keep && block != "") printf "---\n%s", block }
  '
}

note "Dependencies"
for chart in "${CHARTS[@]}"; do
  if [ -f "charts/$chart/Chart.lock" ]; then
    helm dependency build "charts/$chart" >/dev/null
    pass "$chart"
  fi
done

note "helm lint"
for chart in "${CHARTS[@]}"; do
  values=""
  case "$chart" in
    confidential-router-api) values="charts/tests/cases/api-one-model.yaml" ;;
    confidential-router-litellm) values="charts/tests/cases/litellm-one-model.yaml" ;;
    confidential-router-ui) values="charts/tests/cases/ui-default.yaml" ;;
    confidential-s3) values="charts/tests/cases/s3-default.yaml" ;;
  esac
  if output=$(helm lint "charts/$chart" --values "$values" 2>&1); then
    pass "$chart"
  else
    fail "$chart"
    printf '%s\n' "$output" | sed 's/^/        /'
  fi
done

note "helm template goldens"
while IFS=$'\t' read -r name chart repo version; do
  case "$name" in ''|\#*) continue ;; esac
  values="charts/tests/cases/$name.yaml"
  golden="charts/tests/golden/$name.yaml"

  release="$(release_for "$chart")"
  namespace="$(namespace_for "$chart")"

  if [ -n "${repo:-}" ]; then
    rendered=$(helm template "$release" "$chart" --repo "$repo" --version "$version" \
      --namespace "$namespace" --values "$values" 2>&1) || { fail "$name (render)"; printf '%s\n' "$rendered" | sed 's/^/        /'; continue; }
  else
    rendered=$(helm template "$release" "charts/$chart" \
      --namespace "$namespace" --values "$values" 2>&1) || { fail "$name (render)"; printf '%s\n' "$rendered" | sed 's/^/        /'; continue; }
  fi
  filtered=$(printf '%s\n' "$rendered" | own_documents "$chart")

  if [ -n "$update" ]; then
    printf '%s' "$filtered" > "$golden"
    pass "$name (written)"
  elif [ ! -f "$golden" ]; then
    fail "$name (no golden; run UPDATE=1 charts/tests/run.sh)"
  elif diff -u "$golden" <(printf '%s' "$filtered") > /tmp/chart-golden-diff.$$ 2>&1; then
    pass "$name"
  else
    fail "$name"
    sed 's/^/        /' /tmp/chart-golden-diff.$$
  fi
  rm -f /tmp/chart-golden-diff.$$
done < charts/tests/cases.tsv

# The two charts are installed separately and have to be given the same list.
# Nothing enforces that at deploy time, so it is enforced here: the router's
# `litellmModel` and the proxy's `model_name` are one string, and a golden pair
# that disagreed would mean the router advertising a model nothing serves.
note "api and litellm agree on the model names"
for pair in one-model three-models; do
  router=$(grep -o 'litellmModel: "[^"]*"' "charts/tests/golden/api-$pair.yaml" | sed 's/.*: "//; s/"$//' | sort)
  proxy=$(grep -o 'model_name: "[^"]*"' "charts/tests/golden/litellm-$pair.yaml" | sed 's/.*: "//; s/"$//' | sort)
  if [ -n "$router" ] && [ "$router" = "$proxy" ]; then
    pass "$pair ($(printf '%s' "$router" | tr '\n' ' '))"
  else
    fail "$pair: router has [$(printf '%s' "$router" | tr '\n' ' ')], proxy has [$(printf '%s' "$proxy" | tr '\n' ' ')]"
  fi
done

# Each of these is a mistake that deploys cleanly and fails in the cluster, so
# the chart has to refuse it while there is still a human looking.
note "misconfigurations are refused at render time"
refuses() {
  local label="$1"; shift
  local expected="$1"; shift
  if output=$("$@" 2>&1); then
    fail "$label (rendered instead of failing)"
  elif printf '%s' "$output" | grep -q "$expected"; then
    pass "$label"
  else
    fail "$label (failed for the wrong reason)"
    printf '%s\n' "$output" | sed 's/^/        /'
  fi
}

base_api=(helm template "$RELEASE" charts/confidential-router-api --namespace "$NAMESPACE" --values charts/tests/cases/api-one-model.yaml)

refuses "stripe billing with no credentials" "billing.stripe.secretKey" \
  "${base_api[@]}" --set billing.mode=stripe

refuses "production with the console mailer" "written to the log" \
  "${base_api[@]}" --set nodeEnv=production

refuses "a model name the catalogue has no entry for" "modelCatalog has no entry" \
  "${base_api[@]}" --set 'models[0]=llama3.2:4b'

refuses "the same model twice" "twice" \
  "${base_api[@]}" --set 'models[0]=llama3.2:3b' --set 'models[1]=llama3.2:3b'

refuses "the same model twice, on the proxy" "twice" \
  helm template "$RELEASE" charts/confidential-router-litellm --namespace "$NAMESPACE" \
    --set 'models[0]=llama3.2:3b' --set 'models[1]=llama3.2:3b'

refuses "an unpinned image" "pinned by digest" \
  "${base_api[@]}" --set image.digest= --set image.tag=

refuses "the bundled database alongside an external DSN" "postgresql.enabled is true" \
  "${base_api[@]}" --set database.url=postgres://elsewhere/router

refuses "an auth secret too short to sign with" "at least 32 characters" \
  "${base_api[@]}" --set auth.secret=short

refuses "the unimplemented smtp mailer" "not implemented" \
  "${base_api[@]}" --set auth.magicLink.mailer=smtp

refuses "a password floor the router's schema would reject" "between 8 and 128" \
  "${base_api[@]}" --set auth.password.minLength=6

# The bootstrap token creates exactly one account and then stops existing, so a
# deployment with nothing else configured strands even the administrator who
# claimed it — behind a console whose sign-in screen offers nothing.
refuses "every sign-in path switched off at once" "no sign-in path is configured" \
  "${base_api[@]}" --set auth.magicLink.mailer=none --set auth.password.enabled=false

# The console used to have its API origin compiled into its browser bundle, so
# this chart refused to render one pointed anywhere else and the listing was
# capped at the hostname the image was built for. The origin is resolved at run
# time now (SUP-100), and what replaced the refusal is this: the same pinned
# digest, rendered against two unrelated hostnames, follows each of them.
note "one console image serves any API origin"
console_for() {
  helm template "$RELEASE" charts/confidential-router-ui --namespace "$NAMESPACE" \
    --values charts/tests/cases/ui-default.yaml --set "apiHostname=$1" 2>&1
}
env_value() {
  printf '%s\n' "$2" | grep -A1 -- "- name: $1\$" | tail -1 | sed 's/^ *value: //; s/^"//; s/"$//'
}

console_images=""
for host in api.confidential-router.example somewhere.else.example; do
  if ! rendered=$(console_for "$host"); then
    fail "console pointed at $host (render)"
    printf '%s\n' "$rendered" | sed 's/^/        /'
    continue
  fi
  origin=$(env_value ROUTER_UI_API_ORIGIN "$rendered")
  graphql=$(env_value ROUTER_UI_GRAPHQL_HTTP "$rendered")
  console_images="$console_images$(printf '%s\n' "$rendered" | grep -o 'image: .*' | head -1)
"
  if [ "$origin" = "https://$host" ] && [ "$graphql" = "https://$host/graphql" ]; then
    pass "console pointed at $host"
  else
    fail "console pointed at $host: origin [$origin], graphql [$graphql]"
  fi
done

if [ "$(printf '%s' "$console_images" | sort -u | wc -l)" = "1" ]; then
  pass "one image reference for both ($(printf '%s' "$console_images" | sort -u | sed 's/image: //'))"
else
  fail "the two renders pulled different images: $(printf '%s' "$console_images" | tr '\n' ' ')"
fi

# The engine's credential is derived in one place and read in two: the data plane
# holds it, and the Job imports exactly it into Garage. Nothing at deploy time
# checks that the two agree, and a deployment where they do not comes up healthy
# and cannot store a byte — so it is checked here, against the rendered objects.
note "the gateway and the bootstrap Job share one engine credential"
s3_golden=charts/tests/golden/s3-default.yaml
secret_key_id=$(grep -o 'engine-access-key: "[^"]*"' "$s3_golden" | sed 's/.*: "//; s/"$//')
if [ -z "$secret_key_id" ]; then
  fail "the Secret carries no engine-access-key"
elif ! printf '%s' "$secret_key_id" | grep -qE '^GK[0-9a-f]{24}$'; then
  fail "engine-access-key is $secret_key_id, which Garage refuses: GK and 24 hex characters"
else
  pass "engine-access-key is a Garage key id ($secret_key_id)"
fi

# Both readers name the same Secret key rather than a copy of the value, which is
# what makes the paragraph above true by construction rather than by coincidence.
readers=$(grep -c 'key: engine-access-key' "$s3_golden" || true)
if [ "$readers" = "2" ]; then
  pass "the data plane and the Job read it from the same Secret key"
else
  fail "engine-access-key is read from the Secret $readers time(s), expected 2 (the gateway and the Job)"
fi

# The two hostnames are the only fields that differ between two deployments of
# this version, and the chart says so on the objects that carry them. Losing the
# annotation would not fail a render; it would quietly make every deployment
# attest a different digest.
note "both published hostnames are excluded from the evidence digest"
excluded=$(grep -c 'swarm.io/exclude-evidence-fields: "/spec/rules/0/host"' "$s3_golden" || true)
if [ "$excluded" = "2" ]; then
  pass "the console and the S3 Ingress both carry the exclusion"
else
  fail "$excluded of 2 Ingresses carry swarm.io/exclude-evidence-fields"
fi

# The engine holds every internal bucket behind a credential that may create
# buckets. A hostname in front of it would publish that.
note "only the console and the S3 endpoint are published"
hosts=$(grep -A2 '^  rules:' "$s3_golden" | grep -o 'host: .*' | sed 's/host: //; s/"//g' | sort -u | tr '\n' ' ')
if [ "$hosts" = "console.confidential-s3.example s3.confidential-s3.example " ]; then
  pass "hostnames: $hosts"
else
  fail "published hostnames are [$hosts]"
fi

# The chart's own default image references. A listing overrides all of them, so an
# empty or placeholder default is invisible to every check above — and only shows
# up as a `helm install` that refuses to render, or worse, as a chart published to
# an append-only repository with a digest nobody can pull.
note "confidential-s3 pins every image it ships by a real digest"
if output=$(python3 charts/tests/digests.py confidential-s3); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/digests.py"
fi

# What the chart rendered, as a list of objects, parsed the way a cluster parses
# it. A golden diff cannot answer this: a template that loses a `---` glues two
# objects into one document, the golden is regenerated from the same broken
# render so the diff is empty, and `helm lint` reads the merged document as the
# second object and finds nothing wrong. The cluster applies one and drops the
# other. That happened here, to the gateway's Service, and it presented as an S3
# endpoint that answered 503 through an Ingress pointing at nothing.
note "confidential-s3 renders every object it is supposed to"
if output=$(python3 charts/tests/inventory.py confidential-s3 charts/tests/cases/s3-default.yaml); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/inventory.py"
fi

# What `cli/evidence-preview.js` does on the marketplace side, done here on the
# chart: render the same version as two consumers, under two hostnames, in two
# namespaces, and fail on any field that differs and is not declared.
#
# This is the check that catches the mistake nothing else does. A hostname that
# leaks into a manifest outside an Ingress renders perfectly, deploys perfectly,
# and quietly makes every deployment of the version attest a different digest —
# so `expectedDigest` can never be declared, and nobody finds out until somebody
# tries. Exactly one field was found that way: the console's S3_PUBLIC_ENDPOINT.
note "two deployments of this version differ only where the chart says they do"
render_as() {
  helm template "$2" charts/confidential-s3 --namespace "$4" \
    --values charts/tests/cases/s3-default.yaml \
    --set "consoleHostname=console.$1.example" --set "s3Hostname=s3.$1.example" 2>&1
}
# The platform strips /metadata/namespace itself, so a difference there is not
# one this has to declare. Every other differing line has to be an exclusion.
drift_a=$(render_as a cs3 x space-a | grep -v '^  namespace:')
drift_b=$(render_as b cs3 x space-b | grep -v '^  namespace:')
declared='s3.a.example|s3.b.example|console.a.example|console.b.example'
undeclared=$(diff <(printf '%s\n' "$drift_a") <(printf '%s\n' "$drift_b") \
  | grep -E '^[<>]' | grep -vE "$declared" || true)
if [ -z "$undeclared" ]; then
  differing=$(diff <(printf '%s\n' "$drift_a") <(printf '%s\n' "$drift_b") | grep -cE '^<' || true)
  pass "$differing field(s) differ, all of them declared exclusions"
else
  fail "a field differs between two consumers and is not excluded:"
  printf '%s\n' "$undeclared" | sed 's/^/        /'
fi

# Each of those three fields has to actually carry the annotation, in the object
# that holds it. The count above would still pass if an exclusion were dropped
# and the value simply stopped varying for the wrong reason.
# Not that the annotation is present — that the pointer in it still lands on the
# field it names. It is an index into an env list, so adding a variable above
# S3_PUBLIC_ENDPOINT would leave a well-formed exclusion pointing at the wrong
# value, and nothing else in this file would notice.
if python3 - "$s3_golden" <<'PYTHON'
import sys, yaml
golden = list(yaml.safe_load_all(open(sys.argv[1])))
console = next(d for d in golden if d and d["kind"] == "Deployment"
               and d["metadata"]["name"] == "confidential-s3-console")
pointer = console["metadata"]["annotations"]["swarm.io/exclude-evidence-fields"]
node = console
for token in pointer.strip("/").split("/"):
    node = node[int(token)] if isinstance(node, list) else node[token]
env = console["spec"]["template"]["spec"]["containers"][0]["env"]
named = next(e for e in env if e["name"] == "S3_PUBLIC_ENDPOINT")
sys.exit(0 if node == named["value"] else 1)
PYTHON
then
  pass "the console's exclusion pointer resolves to its S3_PUBLIC_ENDPOINT"
else
  fail "the console's swarm.io/exclude-evidence-fields does not point at S3_PUBLIC_ENDPOINT"
fi

# And the listing has to declare the same three, because that is what the
# marketplace reads to show them beside the digest — a digest displayed next to
# "no exclusions" answers the only question that matters about it wrongly.
note "the listing declares the same exclusions the chart annotates"
listing=apps/confidential-s3/app.yaml
for field in \
  '/spec/rules/0/host' \
  '/spec/template/spec/containers/0/env/3/value'
do
  if grep -qF "$field" "$listing"; then
    pass "$field"
  else
    fail "$listing does not declare $field, which the chart excludes"
  fi
done

note "confidential-s3 refuses what would deploy cleanly and not work"
base_s3=(helm template cs3 charts/confidential-s3 --namespace confidential-s3 --values charts/tests/cases/s3-default.yaml)

refuses "one hostname for two ingresses" "must differ" \
  "${base_s3[@]}" --set s3Hostname=console.confidential-s3.example

refuses "an unpinned image" "pinned by digest" \
  "${base_s3[@]}" --set gateway.image.digest= --set gateway.image.tag=

refuses "an ingress with no class" "served by nothing" \
  "${base_s3[@]}" --set gateway.ingress.className=

refuses "a master key seed too short to derive from" "at least 32 characters" \
  "${base_s3[@]}" --set masterKey.seed=short

refuses "a master key that is not 32 bytes" "must decode to 32 bytes" \
  "${base_s3[@]}" --set masterKey.seed= --set masterKey.value=not-a-key

refuses "a master key given twice" "both set" \
  "${base_s3[@]}" --set masterKey.value=8e4f1a2b3c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7

refuses "an engine key Garage would reject" "not a Garage key id" \
  "${base_s3[@]}" --set engine.accessKey=AKIAIOSFODNN7EXAMPLE --set engine.secretKey=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

refuses "half an engine credential" "go together" \
  "${base_s3[@]}" --set engine.accessKey=GK0123456789abcdef01234567

refuses "no first-sign-in token" "nobody can claim the administrator account" \
  "${base_s3[@]}" --set bootstrapToken.value=

refuses "no administrator address" "must be set" \
  "${base_s3[@]}" --set api.bootstrap.adminEmail=

refuses "the bundled database alongside an external DSN" "postgresql.enabled is true" \
  "${base_s3[@]}" --set database.url=postgres://elsewhere/confidential_s3

refuses "a chunk size the gateway would reject" "between 65536 and 67108864" \
  "${base_s3[@]}" --set gateway.chunkSize=1024

# The console reads its two addresses from the environment on every request, so
# one published digest has to serve any hostname. The chart used to be the thing
# that could break that, by baking an origin into the image.
note "one console image serves any hostname"
s3_console_for() {
  helm template cs3 charts/confidential-s3 --namespace confidential-s3 \
    --values charts/tests/cases/s3-default.yaml --set "s3Hostname=$1" 2>&1
}
s3_console_images=""
for host in s3.confidential-s3.example somewhere.else.example; do
  if ! rendered=$(s3_console_for "$host"); then
    fail "console pointed at $host (render)"
    printf '%s\n' "$rendered" | sed 's/^/        /'
    continue
  fi
  endpoint=$(env_value S3_PUBLIC_ENDPOINT "$rendered")
  s3_console_images="$s3_console_images$(printf '%s\n' "$rendered" | grep -o 'image: .*console.*' | head -1)
"
  if [ "$endpoint" = "https://$host" ]; then
    pass "console endpoint for $host"
  else
    fail "console pointed at $host: S3_PUBLIC_ENDPOINT [$endpoint]"
  fi
done
if [ "$(printf '%s' "$s3_console_images" | sort -u | wc -l)" = "1" ]; then
  pass "one image reference for both"
else
  fail "the two renders pulled different console images: $(printf '%s' "$s3_console_images" | tr '\n' ' ')"
fi

note "Result"
if [ "$failures" -eq 0 ]; then
  printf '  everything passed\n\n'
else
  printf '  %s check(s) failed\n\n' "$failures"
  exit 1
fi
