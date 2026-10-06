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
release_for()   { case "$1" in confidential-s3) printf 'cs3' ;; patroni-postgresql) printf 'pg' ;; swarm-model-server) printf 'ms' ;; *) printf 'cr' ;; esac; }
namespace_for() { case "$1" in confidential-s3) printf 'confidential-s3' ;; patroni-postgresql) printf 'patroni' ;; swarm-model-server) printf 'model-server' ;; *) printf 'confidential-router' ;; esac; }

RELEASE=cr
NAMESPACE=confidential-router
CHARTS=(confidential-router-api confidential-router-litellm confidential-router-ui confidential-s3 patroni-postgresql swarm-model-server)

update="${UPDATE:-}"
failures=0

note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

# Only the documents the case's own chart produced. A subchart's output is a
# contract of its own — the vendor's, or in `patroni-postgresql`'s case this
# repository's but under its own goldens — and bumping one would otherwise rewrite
# every golden that installs it. What the seam between the two looks like is checked
# below, against named fields rather than a whole render.
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
    patroni-postgresql) values="charts/tests/cases/patroni-default.yaml" ;;
    swarm-model-server) values="charts/tests/cases/model-server-llama.yaml" ;;
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

# A sign-in link in the pod log is a sign-in link for anyone who can read logs, so
# production mode — which is every deployment of this chart unless `nodeEnv` says
# otherwise — refuses the console mailer.
refuses "the console mailer, whose links land in the pod log" "written to the log" \
  "${base_api[@]}" --set auth.magicLink.mailer=console

# The launch blocker this pair of refusals exists for (SUP-167). `mode: manual`
# mints credit from a signed link; the chart used to deploy it by default and hand
# the container NODE_ENV=development so the API would bind it.
refuses "manual billing, which mints credit from a signed link" "mints credit" \
  "${base_api[@]}" --set billing.mode=manual

refuses "manual billing even with nodeEnv forced back to development" "mints credit" \
  "${base_api[@]}" --set billing.mode=manual --set nodeEnv=development

refuses "Stripe billing outside production mode" "must not run in a mode" \
  "${base_api[@]}" --set billing.mode=stripe \
  --set billing.stripe.secretKey=sk_test_x --set billing.stripe.webhookSecret=whsec_x \
  --set auth.magicLink.mailer=resend --set auth.magicLink.resendApiKey=re_x \
  --set nodeEnv=development

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

# The DSN carries `?sslmode=`, and TypeORM throws the query string away. A database
# told to require TLS therefore refuses every connection the API makes, and says so in
# a log nobody reads before deciding the API is broken.
refuses "a bundled database that requires TLS the API never negotiates" "TypeORM discards" \
  "${base_api[@]}" --set postgresql.requireSsl=true

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

# A URL where a hostname belongs renders `https://https://…` into the CORS list,
# which no browser origin matches: the landing page loads and quietly does
# nothing. The other two are addresses that reach the pod as one comma-separated
# variable and would match nobody once it is split.
refuses "a landing URL where a hostname belongs" "must be a hostname, not a URL" \
  "${base_api[@]}" --set invites.landingHostname=https://landing.confidential-router.example

refuses "an operator name where an address belongs" "not an email address" \
  "${base_api[@]}" --set 'auth.adminEmails[0]=operator'

refuses "two operator addresses in one list entry" "one address per list entry" \
  "${base_api[@]}" --set 'auth.adminEmails[0]=a@example.com\,b@example.com'

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
# The console's two origin variables moved out of the env list and into the
# ConfigMap the evidence snapshot excludes (SUP-211), so they are read from
# `data` rather than from a `- name:` pair. The pod still gets them, through
# `envFrom`, which `charts/tests/evidence_drift.py` and the golden both hold.
config_value() {
  printf '%s\n' "$2" | grep -- "^  $1: " | tail -1 | sed "s/^  $1: //; s/^\"//; s/\"$//"
}

console_images=""
for host in api.confidential-router.example somewhere.else.example; do
  if ! rendered=$(console_for "$host"); then
    fail "console pointed at $host (render)"
    printf '%s\n' "$rendered" | sed 's/^/        /'
    continue
  fi
  origin=$(config_value ROUTER_UI_API_ORIGIN "$rendered")
  graphql=$(config_value ROUTER_UI_GRAPHQL_HTTP "$rendered")
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

# The three settings a launch needs, each of which was missing from a real
# deployment and broke something the campaign depended on (SUP-154). A golden diff
# would catch a change to any of them, but not the property that matters: which
# object each one lands in. The rendered config is attested and readable inside the
# published evidence bundle, so an operator address or an ingest key that drifted
# into it would be public — and the address would make the evidence digest a
# property of who deployed it (SUP-124).
note "a campaign deployment renders its three settings, each in the right object"
campaign=charts/tests/golden/api-campaign.yaml
config=$(sed -n '/^  router.yaml: |/,/^---$/p' "$campaign")
public=$(sed -n '/^  ROUTER_/p' "$campaign")

# The two origins and the landing URL used to be literals in `router.yaml`, which
# made the attested document — and so the evidence digest — a property of the
# hostnames the operator chose (SUP-210). They are in the excluded public
# ConfigMap now, and the attested document names them by placeholder; both halves
# are asserted, because a placeholder nothing fills is a boot the router refuses.
if printf '%s\n' "$public" | grep -q 'ROUTER_VALID_CLIENT_ORIGINS: "https://console.confidential-router.example,https://landing.confidential-router.example"'; then
  pass "validClientOrigins carries the console and the landing page, in that order"
else
  fail "ROUTER_VALID_CLIENT_ORIGINS does not carry both origins"
fi

if printf '%s\n' "$config" | grep -q 'validClientOrigins: "${ROUTER_VALID_CLIENT_ORIGINS}"'; then
  pass "the attested config refers to the origin list rather than carrying it"
else
  fail "the attested router.yaml does not refer to ROUTER_VALID_CLIENT_ORIGINS"
fi

if printf '%s\n' "$public" | grep -q 'ROUTER_LANDING_BASE_URL: "https://landing.confidential-router.example"'; then
  pass "invites.landingBaseUrl points at the landing page, not the schema default"
else
  fail "the rendered config has no landing base URL"
fi

if printf '%s\n' "$config" | grep -q 'landingBaseUrl: "${ROUTER_LANDING_BASE_URL}"'; then
  pass "the attested config refers to the landing origin rather than carrying it"
else
  fail "the attested router.yaml does not refer to ROUTER_LANDING_BASE_URL"
fi

# And the point of the whole split: no hostname of this deployment is left in the
# document the snapshot attests. A literal that came back would render, deploy and
# work, and silently make the digest a property of the hostname again.
for host in \
  api.confidential-router.example \
  console.confidential-router.example \
  landing.confidential-router.example
do
  if printf '%s\n' "$config" | grep -q -- "$host"; then
    fail "the attested router.yaml carries $host, which makes the evidence digest a property of it"
  else
    pass "$host is not in the attested router.yaml"
  fi
done

for leak in operator@confidential-router.example phc_golden_test_project_key; do
  if printf '%s\n' "$config" | grep -q -- "$leak"; then
    fail "the rendered router.yaml carries $leak, which the evidence bundle publishes"
  else
    pass "$leak is not in the rendered router.yaml"
  fi
done

# Both addresses in one variable, in the order they were given: the router splits
# a comma-separated value back into the list.
if grep -q 'admin-emails: "operator@confidential-router.example,second.operator@confidential-router.example"' "$campaign"; then
  pass "both operator addresses reach the Secret as one comma-separated value"
else
  fail "the Secret does not carry both operator addresses"
fi
if grep -q 'posthog-project-key: "phc_golden_test_project_key"' "$campaign"; then
  pass "the ingest key reaches the Secret"
else
  fail "the Secret does not carry the PostHog project key"
fi

# And the pod has to actually read them from there. A Secret nothing references is
# a deployment where all three settings are configured and none of them is applied.
for variable in CR_API_AUTH__ADMIN_EMAILS POSTHOG_PROJECT_KEY; do
  if [ "$(grep -c -- "- name: $variable\$" "$campaign")" = "2" ]; then
    pass "$variable is read from the Secret by both the server and the migration container"
  else
    fail "$variable is read $(grep -c -- "- name: $variable\$" "$campaign") time(s), expected 2"
  fi
done

# Nothing of the three is rendered for a deployment that asked for none of them:
# the router's config schema is strict, so a key an older image has never heard of
# is a boot it refuses rather than a value it ignores.
note "a deployment with no campaign renders none of it"
plain=charts/tests/golden/api-one-model.yaml
for absent in 'invites:' CR_API_AUTH__ADMIN_EMAILS POSTHOG_PROJECT_KEY admin-emails posthog-project-key; do
  if grep -q -- "$absent" "$plain"; then
    fail "api-one-model renders $absent"
  else
    pass "no $absent"
  fi
done

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

# The seam between the listing and the chart, which the two-consumer render below
# cannot see: it renders the chart with fixed values, while `consumer.*` is
# resolved by the marketplace when it turns a definition into values. A consumer
# address that lands in an attested manifest as a literal renders, deploys and
# works — and makes the evidence digest a property of who deployed it. That is
# what 0.1.0 did, and a real deployment is what found it.
note "no attested object carries a consumer value as a literal"
if output=$(python3 charts/tests/consumer_fields.py confidential-s3 charts/tests/cases/s3-default.yaml); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/consumer_fields.py"
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

# The database and the application that connects to it are two charts, and nothing at
# deploy time checks that they agree. The api chart's goldens deliberately do not
# carry the subchart's documents, so these read the composed render instead — field
# by field, because what matters is a handful of strings that have to be identical.
note "the router and its database agree on where the database is"
composed=$(helm template "$RELEASE" charts/confidential-router-api --namespace "$NAMESPACE" \
  --values charts/tests/cases/api-one-model.yaml 2>&1) || { fail "composed render"; printf '%s\n' "$composed" | sed 's/^/        /'; }

if output=$(python3 charts/tests/router_database.py <<<"$composed"); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/router_database.py"
fi

# The database half of this listing has to be the same for everybody, or no version of
# it can ever declare an `expectedDigest`. This asks only about that half, by name: the
# api half is a question of its own, and `evidence_drift.py` below asks it with the
# chart's declared exclusions taken into account.
note "the database attests the same thing for every consumer"
if output=$(python3 charts/tests/database_drift.py confidential-router-api charts/tests/cases/api-one-model.yaml postgresql); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/database_drift.py"
fi

# Same reasoning as confidential-s3's: a golden diff cannot see a lost `---`, and the
# database is the component where an object silently not being applied is a database
# that comes up and cannot elect anybody.
note "confidential-router-api renders every object it is supposed to, database included"
if output=$(python3 charts/tests/inventory.py confidential-router-api charts/tests/cases/api-one-model.yaml); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/inventory.py confidential-router-api"
fi

note "patroni-postgresql pins every image it ships by a real digest"
if output=$(python3 charts/tests/digests.py patroni-postgresql); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/digests.py patroni-postgresql"
fi

note "patroni-postgresql renders every object it is supposed to"
if output=$(python3 charts/tests/inventory.py patroni-postgresql charts/tests/cases/patroni-default.yaml); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "charts/tests/inventory.py patroni-postgresql"
fi

# Each of these is a database that deploys cleanly and then loses data, or never
# comes up at all, in a way no render error would have mentioned.
note "patroni-postgresql refuses what would deploy cleanly and not be durable"
base_pg=(helm template pg charts/patroni-postgresql --namespace patroni --values charts/tests/cases/patroni-default.yaml)

refuses "synchronous replication on a single instance" "no standby for a commit" \
  "${base_pg[@]}" --set replicaCount=1

refuses "more synchronous standbys than there are instances" "cannot be its own standby" \
  "${base_pg[@]}" --set patroni.synchronousNodeCount=3

refuses "strict synchronous mode without synchronous mode" "property of synchronous replication" \
  "${base_pg[@]}" --set patroni.synchronousMode=false --set patroni.synchronousModeStrict=true

refuses "a disruption budget that permits the whole cluster" "not a budget" \
  "${base_pg[@]}" --set podDisruptionBudget.maxUnavailable=3

refuses "no replication password" "cannot stream from the leader" \
  "${base_pg[@]}" --set auth.replication.password=

refuses "an application role with no password" "auth.password is empty" \
  "${base_pg[@]}" --set auth.password=

refuses "an application role with nowhere to connect" "role is created together with the database" \
  "${base_pg[@]}" --set auth.database=

refuses "the application connecting as the superuser" "unrestricted rights" \
  "${base_pg[@]}" --set auth.username=postgres

refuses "an application role name that would have to be quoted" "lowercase letters, digits" \
  "${base_pg[@]}" --set 'auth.username=Robert");DROP'

refuses "an unpinned image" "pinned by digest" \
  "${base_pg[@]}" --set image.digest= --set image.tag=

# The DCS and the router can make the database unreachable as thoroughly as the
# database can, and each of these is a way of doing it that renders perfectly.
refuses "an even number of etcd members" "strictly worse than an odd count" \
  "${base_pg[@]}" --set etcd.replicaCount=2

refuses "a DCS disruption budget that permits the quorum" "nobody can renew" \
  "${base_pg[@]}" --set etcd.podDisruptionBudget.maxUnavailable=2

refuses "a router budget that permits every router" "nothing can reach" \
  "${base_pg[@]}" --set router.podDisruptionBudget.maxUnavailable=2

refuses "two router listeners on one port" "cannot bind the same port" \
  "${base_pg[@]}" --set router.standbyPort=5432

refuses "a database with no router at all" "no path around it" \
  "${base_pg[@]}" --set router.replicaCount=0

refuses "an unpinned DCS image" "etcd.image.digest is empty" \
  "${base_pg[@]}" --set etcd.image.digest= --set etcd.image.tag=

refuses "an unpinned router image" "router.image.digest is empty" \
  "${base_pg[@]}" --set router.image.digest= --set router.image.tag=

refuses "an unpinned bootstrap image" "etcd.bootstrapImage.digest is empty" \
  "${base_pg[@]}" --set etcd.bootstrapImage.digest= --set etcd.bootstrapImage.tag=

refuses "replacing the one-per-node rule by accident" "the one that applies" \
  "${base_pg[@]}" --set 'affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].weight=1'

# Every `${…}` in the attested config is filled by something the pod is given, and
# nothing excluded from the snapshot is dead weight. This is the failure mode the
# hostname split introduced: a renamed variable is not a bad render, it is a
# config loader that throws before the first listener — the whole deployment is a
# crash loop and no golden diff would have mentioned it.
note "every placeholder in the attested config has something that fills it"
for case in api-one-model api-campaign api-billing-stripe api-no-models api-endpoint-hostname; do
  if output=$(python3 charts/tests/config_placeholders.py confidential-router-api "charts/tests/cases/$case.yaml"); then
    printf '%s\n' "$output" | sed "s/\$/ ($case)/"
  else
    printf '%s\n' "$output"
    fail "charts/tests/config_placeholders.py $case"
  fi
done

# The property this listing exists to have (SUP-211): two deployments of this
# version, under two hostnames in two namespaces, differ only in fields the chart
# excludes and the definition declares. Field by field rather than line by line,
# which is what catches a key that is absent on one side and a pointer that has
# gone stale — see the module docstring.
note "two deployments of the router differ only where the chart says they do"
drift_case() {
  if output=$(python3 charts/tests/evidence_drift.py "$1" "charts/tests/cases/$2.yaml" confidential-router \
      'apiHostname=api.{t}.example' 'consoleHostname=console.{t}.example' "${@:3}"); then
    printf '%s\n' "$output" | sed "s/\$/ ($1, $2)/"
  else
    printf '%s\n' "$output"
    fail "charts/tests/evidence_drift.py $1 $2"
  fi
}
drift_case confidential-router-api api-one-model
drift_case confidential-router-api api-campaign 'invites.landingHostname=landing.{t}.example'
drift_case confidential-router-ui ui-default
# ---------------------------------------------------------------------------
# swarm-model-server. Every one of these is a deployment that renders cleanly
# and then serves something it should not, or nothing at all.
# ---------------------------------------------------------------------------
base_ms=(helm template ms charts/swarm-model-server --namespace model-server --values charts/tests/cases/model-server-llama.yaml)

# The one that matters most: a public hostname in front of a GPU with no
# credential is the deployment handed to whoever finds the name.
refuses "an inference endpoint published with no key" "does not publish an unauthenticated" \
  "${base_ms[@]}" --set apiKey= --set existingSecret=

refuses "an ingress with no hostname" "hostname is empty" \
  "${base_ms[@]}" --set hostname=

# The weights pin. Without it the deployment serves whatever the network
# returned, and the evidence says nothing about which bytes those were.
refuses "a weights manifest with no files" "will not serve weights it cannot verify" \
  "${base_ms[@]}" --set 'model.weights.files=null'

refuses "a branch name where a commit belongs" "not a 40-character commit sha" \
  "${base_ms[@]}" --set model.weights.revision=main

refuses "a weight file with no sha256" "an unverifiable file is the whole problem" \
  "${base_ms[@]}" --set 'model.weights.files[0].sha256='

refuses "a totalBytes that does not match the files" "edited by hand" \
  "${base_ms[@]}" --set model.weights.totalBytes=1234

# Fails after the download rather than before it, with the volume full.
refuses "a volume too small for the weights" "the download would fail with the volume full" \
  "${base_ms[@]}" --set persistence.size=2Gi

# An engine started with a parser it does not register exits at startup; an
# engine started with the *wrong* parser turns every tool call into prose.
refuses "a tool-call parser the engine does not register" "is not one of the parsers vLLM" \
  "${base_ms[@]}" --set model.toolCalling.parser=llama3-json

# The published engine image is a CUDA build. Without a card the pod starts,
# finds no device, and restarts for ever.
refuses "a GPU-less deployment of a CUDA-only engine" "does not serve on a CPU" \
  "${base_ms[@]}" --set gpu.enabled=false

refuses "an unpinned engine image" "pinned by digest" \
  "${base_ms[@]}" --set image.digest= --set image.tag=

# A model id travels in the connection link's fragment and in an OpenAI request
# body; a space in it breaks both.
refuses "a model id that would break the connection link" "limited to letters, digits" \
  "${base_ms[@]}" --set 'model.id=my model'

refuses "a model with no id at all" "model.id is empty" \
  "${base_ms[@]}" --set model.id=

refuses "TLS switched on with no certificate" "ingress.tls.secretName is empty" \
  "${base_ms[@]}" --set ingress.tls.enabled=true

# SUP-230: a chat template inlined in a listing renders here and is refused by the
# marketplace's publish parse, a long way from whoever wrote it. The chart refuses
# it where the message can say what to do instead.
refuses "a chat template passed inline instead of by file name" "model.chatTemplate is gone" \
  "${base_ms[@]}" --set 'model.chatTemplate=hello {{ bos_token }}'

refuses "a chat template file the chart does not carry" "does not exist" \
  "${base_ms[@]}" --set model.chatTemplateFile=not-here.jinja

# The three things a published endpoint must not expose, asserted on the render
# rather than trusted to the values file.
note "swarm-model-server publishes only what authenticates"
for case in model-server-llama model-server-gemma model-server-qwen-fp8; do
  rendered=$(helm template ms charts/swarm-model-server --namespace model-server \
    --values "charts/tests/cases/$case.yaml" 2>&1) || { fail "$case (render)"; continue; }
  problems=""
  # Every ingress path is under /v1: vLLM authenticates /v1 and leaves
  # /metrics, /docs and /tokenize open.
  paths=$(printf '%s\n' "$rendered" | awk '/^kind: Ingress$/,0' | grep -oE '^\s+- path: .*' | sed 's/.*path: //; s/"//g')
  for path in $paths; do
    case "$path" in /v1*) ;; *) problems="$problems published-path:$path" ;; esac
  done
  [ -n "$paths" ] || problems="$problems no-ingress-paths"
  # The key is referenced, never written into an argument or a literal env value.
  printf '%s\n' "$rendered" | grep -q 'secretKeyRef' || problems="$problems key-not-by-reference"
  # A list item, not a mention: the chart's own comment explains why the flag is
  # not used, and a bare substring match would flag that comment.
  printf '%s\n' "$rendered" | grep -qE '^\s+- "?--api-key' && problems="$problems key-on-the-command-line"
  # Every container that is not the engine asks for zero GPUs explicitly: a GPU
  # space's LimitRange defaults a missing nvidia.com/gpu to the reserved count.
  printf '%s\n' "$rendered" | grep -q 'nvidia.com/gpu: "0"' || problems="$problems fetcher-wants-a-gpu"
  # The hostname is excluded from the evidence snapshot, or the digest is a
  # property of the deployment rather than of the version.
  printf '%s\n' "$rendered" | grep -q 'swarm.io/exclude-evidence-fields' || problems="$problems hostname-not-excluded"
  if [ -z "$problems" ]; then pass "$case"; else fail "$case:$problems"; fi
done

# The same check for the model-serving family. The release name is fixed per
# listing (`releasePrefix = shortName(listing.name)` on the marketplace side), so
# it is held constant here and only the things a deployment really chooses — the
# namespace, the hostname and the generated key — are varied.
note "two deployments of a model listing differ only in the hostname"
for case in model-server-llama model-server-gemma model-server-qwen-fp8; do
  render_ms() {
    helm template ms-fixed charts/swarm-model-server --namespace "$2" \
      --values "charts/tests/cases/$case.yaml" \
      --set "hostname=$1.conf-apps.example" \
      --set "apiKey=$3" 2>&1 | grep -v '^  namespace:'
  }
  a=$(render_ms alpha space-a KEYAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)
  b=$(render_ms beta space-b KEYBBBBBBBBBBBBBBBBBBBBBBBBBBBBB)
  # The key is in a Secret, whose contents the platform lifts out before the
  # snapshot is taken, so a difference there is not one this has to declare.
  declared='alpha.conf-apps.example|beta.conf-apps.example|KEYAAAAAAAAAAAAAAAAAAAAAAAAAAAAA|KEYBBBBBBBBBBBBBBBBBBBBBBBBBBBBB'
  undeclared=$(diff <(printf '%s\n' "$a") <(printf '%s\n' "$b") | grep -E '^[<>]' | grep -vE "$declared" || true)
  if [ -z "$undeclared" ]; then
    pass "$case"
  else
    fail "$case: a field differs between two consumers and is not excluded:"
    printf '%s\n' "$undeclared" | sed 's/^/        /'
  fi
done

# The exclusion has to be declared in both places: the annotation is what the
# cloud acts on, and `evidence.exclude` in the listing is what the marketplace
# reads to show it beside the digest.
note "each model listing declares the exclusion its chart annotates"
# Each listing against its own golden. Feeding one golden to all three would
# still check every listing's declaration, but would only ever resolve the
# llama render's pointer — and a pointer is an index, so the whole point of
# resolving it is that it is checked against the object it indexes.
for pair in "llama-3-2-3b-instruct:model-server-llama" \
            "gemma-2-2b-it:model-server-gemma" \
            "qwen3-coder-30b-a3b-instruct-fp8:model-server-qwen-fp8"; do
  app="${pair%%:*}"
  case_name="${pair##*:}"
  if output=$(python3 - "apps/$app/app.yaml" "charts/tests/golden/$case_name.yaml" <<'PYTHON'
import sys, yaml
definition = yaml.safe_load(open(sys.argv[1]))
golden = [d for d in yaml.safe_load_all(open(sys.argv[2])) if d]
ingress = next(d for d in golden if d["kind"] == "Ingress")
annotated = ingress["metadata"]["annotations"]["swarm.io/exclude-evidence-fields"]
pointers = [p.strip() for p in annotated.split(",") if p.strip()]

# Every pointer has to resolve, and to resolve to the hostname it claims to
# name. They are indexes, so a second rule added above would leave a
# well-formed exclusion pointing at the wrong value and nothing else here
# would notice.
host = ingress["spec"]["rules"][0]["host"]
for pointer in pointers:
    node = ingress
    for token in pointer.strip("/").split("/"):
        node = node[int(token)] if isinstance(node, list) else node[token]
    if node != host:
        sys.exit(f"{pointer} resolves to {node!r}, not the ingress host {host!r}")

# Every place the hostname appears in this object has to be one of them.
def host_pointers(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from host_pointers(value, f"{prefix}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from host_pointers(value, f"{prefix}/{index}")
    elif node == host:
        yield prefix

found = set(host_pointers(ingress["spec"], "/spec"))
missing = found - set(pointers)
if missing:
    sys.exit(f"the hostname also appears at {sorted(missing)}, which is not excluded")

declared = definition.get("evidence", {}).get("exclude", [])
fields = {f for entry in declared for f in entry.get("fields", [])}
for pointer in pointers:
    if pointer not in fields:
        sys.exit(f"the listing does not declare {pointer}: {sorted(fields)}")
names = {entry["match"].get("name") for entry in declared}
if ingress["metadata"]["name"] not in names:
    sys.exit(f"the listing's exclusion matches {names}, not {ingress['metadata']['name']}")
print(f"{len(pointers)} pointer(s), all resolved and declared")
PYTHON
  ); then
    pass "$app ($output)"
  else
    fail "$app: $output"
  fi
done

# TLS is off in every listing because the platform terminates it, but if anyone
# turns it on the hostname appears a second time under /spec/tls/0/hosts/0 — and
# one unexcluded copy is enough to make the digest a property of the deployment.
note "the hostname stays excluded when TLS is switched on"
tls_render="$(mktemp)"
if helm template ms charts/swarm-model-server --namespace model-server \
      --values charts/tests/cases/model-server-llama.yaml \
      --set ingress.tls.enabled=true --set ingress.tls.secretName=tls > "$tls_render" 2>&1 \
   && python3 - "$tls_render" <<'PYTHON'
import sys, yaml
ingress = next(d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d["kind"] == "Ingress")
pointers = {p.strip() for p in
            ingress["metadata"]["annotations"]["swarm.io/exclude-evidence-fields"].split(",")}
host = ingress["spec"]["rules"][0]["host"]
if ingress["spec"]["tls"][0]["hosts"] != [host]:
    sys.exit("the TLS block does not carry the hostname this test assumes")
for expected in ("/spec/rules/0/host", "/spec/tls/0/hosts/0"):
    if expected not in pointers:
        sys.exit(f"{expected} is not excluded: {sorted(pointers)}")
PYTHON
then
  pass "both /spec/rules/0/host and /spec/tls/0/hosts/0 are excluded"
else
  fail "a TLS-enabled render leaves the hostname in the snapshot"
  sed 's/^/        /' "$tls_render"
fi
rm -f "$tls_render"

# The fetcher is what stands between the manifest and the GPU, so its refusals
# are tested rather than read. It is loaded from the chart's files/ directory —
# the same bytes the ConfigMap carries — and handed manifests it must reject.
note "the weights fetcher refuses what the manifest should not be able to say"
if output=$(python3 charts/tests/fetcher_guard.py 2>&1); then
  pass "$(printf '%s' "$output" | tail -1)"
else
  fail "charts/tests/fetcher_guard.py"
  printf '%s\n' "$output" | sed 's/^/        /'
fi

# `.Files.Get` must hand the template over verbatim. If Helm ever rendered it —
# or if someone "fixed" the braces by escaping them — the model would be served a
# template with holes in it, and nothing downstream would say so.
note "the chat template reaches the ConfigMap unrendered"
if output=$(python3 - <<'PYTHON'
import subprocess, sys, yaml, pathlib
rendered = subprocess.run(
    ["helm", "template", "ms", "charts/swarm-model-server", "--namespace", "model-server",
     "--values", "charts/tests/cases/model-server-gemma.yaml"],
    capture_output=True, text=True, check=True).stdout
docs = [d for d in yaml.safe_load_all(rendered) if d]
cm = next((d for d in docs
           if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("chat-template")), None)
if cm is None:
    sys.exit("the gemma case rendered no chat-template ConfigMap")
served = cm["data"]["chat-template.jinja"].rstrip("\n")
source = pathlib.Path(
    "charts/swarm-model-server/files/chat-templates/gemma-2.jinja").read_text().rstrip("\n")
if served != source:
    sys.exit("the rendered template differs from the file in the chart")
for needed in ("{{ bos_token }}", "{%- if messages[0]['role'] == 'system' -%}"):
    if needed not in served:
        sys.exit(f"{needed!r} did not survive into the ConfigMap")
# The engine has to be told to use it.
deployment = next(d for d in docs if d["kind"] == "Deployment")
args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
if "--chat-template" not in args:
    sys.exit("the ConfigMap is rendered but the engine is never pointed at it")
print(f"{len(served)} bytes, byte-identical to the file, and the engine is pointed at it")
PYTHON
); then
  pass "$output"
else
  fail "$output"
fi

note "the connection link matches its specification"
if output=$(python3 charts/tests/smoke/model-server.py --self-test 2>&1); then
  pass "docs/model-connection-link.md test vectors ($(printf '%s' "$output" | grep -c '  ok ') vectors)"
else
  fail "docs/model-connection-link.md test vectors"
  printf '%s\n' "$output" | sed 's/^/        /'
fi

for app in llama-3-2-3b-instruct gemma-2-2b-it qwen3-coder-30b-a3b-instruct-fp8; do
  if output=$(python3 - "apps/$app/app.yaml" <<'PYTHON'
import pathlib, sys, yaml
sys.path.insert(0, "charts/tests/smoke")
# The checker is a script, not a module; load it by path so the parser under test
# is literally the one the smoke run uses.
import importlib.util
spec = importlib.util.spec_from_file_location("ms", "charts/tests/smoke/model-server.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

definition = yaml.safe_load(open(sys.argv[1]))
outputs = {o["id"]: o for o in definition["outputs"]}
link_output = outputs.get("connectionLink")
if not link_output:
    sys.exit("the listing emits no connectionLink output")
if link_output.get("type") != "secret":
    sys.exit(f"connectionLink is type {link_output.get('type')!r}, not 'secret': a link is a "
             f"credential with a URL around it")

# What the marketplace will substitute: a hostname and a generated key of the
# shape the platform's generator emits (A-Za-z0-9, >= 32).
key = "GENERATEDkey0123456789abcdefABCD"
link = (link_output["value"]
        .replace("{{ params.apiHostname }}", "m.conf-apps.example")
        .replace("{{ params.apiKey }}", key))
if "{{" in link:
    sys.exit(f"an expression was left unsubstituted: {link}")

base, model, parsed_key = ms.parse_connection_link(link)
if parsed_key != key:
    sys.exit(f"the key round-tripped as {parsed_key!r}")
declared = definition["components"][0]["deployment"]["values"]["base"]["model"]["id"]
if model != declared:
    sys.exit(f"the link says model={model!r} but the chart serves {declared!r}")
if base != "https://m.conf-apps.example/v1":
    sys.exit(f"unexpected base URL {base!r}")

# The key parameter has to constrain its alphabet, because an output template has
# no percent-encoder: punctuation in a key would split the link on its own
# separators.
params = {p["id"]: p for p in definition["parameters"]}
pattern = params["apiKey"].get("validation", {}).get("pattern")
if pattern != "^[A-Za-z0-9]+$":
    sys.exit(f"apiKey's pattern is {pattern!r}; the link needs a URL-safe alphabet")
print(f"model={model} key={len(parsed_key)} chars")
PYTHON
  ); then
    pass "$app emits a parseable link ($output)"
  else
    fail "$app: $output"
  fi
done

note "Result"
if [ "$failures" -eq 0 ]; then
  printf '  everything passed\n\n'
else
  printf '  %s check(s) failed\n\n' "$failures"
  exit 1
fi
