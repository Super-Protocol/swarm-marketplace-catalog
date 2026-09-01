#!/usr/bin/env bash
#
# Validates every AppDefinition in apps/ against the marketplace specification's
# JSON Schemas, with ajv.
#
#   apps/tests/run.sh                                    # fetch the pinned spec, validate
#   SPEC_DIR=../swarm-marketplace-spec apps/tests/run.sh  # validate against a checkout
#   SPEC_REF=<sha-or-branch> apps/tests/run.sh            # against another revision
#
# The schemas are not vendored here. A copy in this repository is a second
# contract that drifts from the first one silently, and the drift is exactly what
# this check exists to catch — so the revision is pinned below and moved
# deliberately.
#
# Schema validity is not the whole contract, so paths.py runs after it: it checks
# what the schemas cannot, namely that a patch has something to patch and that a
# base key reaches a value the chart actually declares.
#
# Only apps/ is covered. datasets/ and interfaces/ have schemas of their own and
# no check yet; adding them is the same shape of work.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

# swarm-marketplace-spec, the revision these listings are written against:
# `widget: hostname` (SUP-104), on top of `type: array` + `widget: multiselect`
# (SUP-98).
SPEC_REF="${SPEC_REF:-db8e2af8602098dc39a383a2a63467067f8faaa3}"
SPEC_REPO="${SPEC_REPO:-Super-Protocol/swarm-marketplace-spec}"

# Listings that predate strict validation and do not pass it (SUP-99). They are
# recorded rather than fixed here: whoever republishes one owns its migration,
# and a definition that starts passing has to be taken off this list — which is
# how the debt gets paid rather than forgotten.
#
#   conversational-analyst            a top-level `evidence` block, which the
#                                     platform implements but the published
#                                     schemas do not carry yet.
#
# confluent-platform and ollama-webui were here for `widget: hostname`, which the
# specification did not define. It defines it as of the revision pinned above
# (SUP-104), and both now validate — so they come off the list rather than stay
# on it as debt nobody owes.
KNOWN_INVALID=(
  apps/conversational-analyst/app.yaml
)

failures=0
note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failures=$((failures + 1)); }

is_known_invalid() {
  local candidate="$1" entry
  for entry in "${KNOWN_INVALID[@]}"; do
    [ "$entry" = "$candidate" ] && return 0
  done
  return 1
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

note "Specification schemas"
if [ -n "${SPEC_DIR:-}" ]; then
  schemas="$(cd "$SPEC_DIR/specs" && pwd)"
  pass "from $SPEC_DIR"
else
  curl -sfL --retry 3 --max-time 60 \
    "https://codeload.github.com/${SPEC_REPO}/tar.gz/${SPEC_REF}" \
    | tar -xz -C "$work"
  schemas="$(echo "$work"/swarm-marketplace-spec-*/specs)"
  pass "$SPEC_REPO @ ${SPEC_REF:0:7}"
fi

note "ajv"
npm install --silent --no-save --no-audit --no-fund \
  --prefix "$work" ajv-cli@5.0.0 ajv-formats@2.1.1
ajv="$work/node_modules/.bin/ajv"
pass "$("$ajv" --version 2>/dev/null || echo installed)"

# Every schema except the root is registered by $id, which is what the relative
# $refs inside them resolve to.
refs=()
for schema in "$schemas"/*.schema.json; do
  [ "$(basename "$schema")" = "app-definition.schema.json" ] && continue
  refs+=(-r "$schema")
done

note "apps/*/app.yaml"
for definition in apps/*/app.yaml; do
  [ -f "$definition" ] || continue
  if output=$("$ajv" validate --spec=draft7 --errors=text -c ajv-formats \
      -s "$schemas/app-definition.schema.json" "${refs[@]}" -d "$definition" 2>&1); then
    if is_known_invalid "$definition"; then
      fail "$definition now validates — remove it from KNOWN_INVALID in $0"
    else
      pass "$definition"
    fi
  elif is_known_invalid "$definition"; then
    pass "$definition (known-invalid, SUP-99)"
  else
    fail "$definition"
    printf '%s\n' "$output" | sed 's/^/        /'
  fi
done

note "patch targets and base keys"
if output=$(python3 "$here/paths.py"); then
  printf '%s\n' "$output"
else
  printf '%s\n' "$output"
  fail "apps/tests/paths.py"
fi

note "Result"
if [ "$failures" -eq 0 ]; then
  printf '  everything passed\n\n'
else
  printf '  %s check(s) failed\n\n' "$failures"
  exit 1
fi
