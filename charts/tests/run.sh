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

RELEASE=cr
NAMESPACE=confidential-router
CHARTS=(confidential-router-api confidential-router-litellm confidential-router-ui)

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

  if [ -n "${repo:-}" ]; then
    rendered=$(helm template "$RELEASE" "$chart" --repo "$repo" --version "$version" \
      --namespace "$NAMESPACE" --values "$values" 2>&1) || { fail "$name (render)"; printf '%s\n' "$rendered" | sed 's/^/        /'; continue; }
  else
    rendered=$(helm template "$RELEASE" "charts/$chart" \
      --namespace "$NAMESPACE" --values "$values" 2>&1) || { fail "$name (render)"; printf '%s\n' "$rendered" | sed 's/^/        /'; continue; }
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

note "Result"
if [ "$failures" -eq 0 ]; then
  printf '  everything passed\n\n'
else
  printf '  %s check(s) failed\n\n' "$failures"
  exit 1
fi
