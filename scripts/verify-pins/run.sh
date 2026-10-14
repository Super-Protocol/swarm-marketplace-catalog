#!/usr/bin/env bash
#
# The self-test for verify_pins.py: one fixture repository per shape of mistake,
# and a recorded attestation lookup for each, so the gate's own behaviour is
# checked without the network and without waiting on ghcr.io.
#
#   scripts/verify-pins/run.sh          # the fixtures (offline, ~1s)
#   scripts/verify-pins/run.sh --live   # the fixtures, then this working tree against real gh
#
# Why recordings rather than live lookups: three of the four failing shapes cannot
# be produced live at all. Nobody can push an image to ghcr.io/super-protocol that
# has no attestation, or an attestation whose predicate disagrees with its own
# signing certificate — which is exactly why those shapes have never been tested
# and exactly what a provenance gate has to get right. The recordings are the real
# `gh attestation verify --format json` output, trimmed to the fields a verdict
# reads (`verify_pins.py --record` writes them), with the failing cases edited from
# there.
#
# fixtures/main-attestations.json is the recording of the pins on main. Re-record it
# when a listing re-pins, from a tree where the live gate passes:
#
#   scripts/verify-pins/verify_pins.py --record scripts/verify-pins/fixtures/main-attestations.json
#
# and update the digests and the commit in fixtures/*/ to match — good-train is the
# regression that says the 0.18.3 train traces to one commit, and it is only that
# as long as it carries that train's digests.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

verifier="$here/verify_pins.py"
fixtures="$here/fixtures"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failures=0

# Each case runs the gate against fixtures/<case>/, with the recorded lookups of
# main overlaid by fixtures/<case>/attestations.json when there is one, and asserts
# the exit status and every string a reader would need the output to name.
case_run() {
  local name="$1" expected="$2"
  shift 2

  local attestations="$work/$name.json"
  python3 - "$fixtures/main-attestations.json" "$fixtures/$name/attestations.json" "$attestations" <<'PY'
import json, sys
base = json.load(open(sys.argv[1]))
try:
    base.update(json.load(open(sys.argv[2])))
except FileNotFoundError:
    pass
json.dump(base, open(sys.argv[3], "w"))
PY

  local output="$work/$name.out"
  local status=0
  python3 "$verifier" --root "$fixtures/$name" --attestations "$attestations" \
    --summary "$work/$name.md" >"$output" 2>&1 || status=$?

  if [ "$status" != "$expected" ]; then
    echo "  FAIL  $name: exit $status, expected $expected"
    sed 's/^/        /' "$output"
    failures=$((failures + 1))
    return
  fi

  local missing=0
  for needle in "$@"; do
    if ! grep -qF -- "$needle" "$output"; then
      echo "  FAIL  $name: the output never says \"$needle\""
      missing=1
    fi
  done
  if [ "$missing" != 0 ]; then
    sed 's/^/        /' "$output"
    failures=$((failures + 1))
    return
  fi

  echo "  ok    $name"
}

echo "verify-pins fixtures"

# The train on main. Three first-party pins, one source revision, and the commit
# printed in full — a reviewer reads this instead of re-running anything, so the
# whole commit has to be in the output and not an abbreviation of it.
case_run good-train 0 \
  "82769ff3fc764ab9c6e176280874175a4e5c09b4" \
  "charts/confidential-router-api/values.yaml: image" \
  "charts/confidential-router-api/values.yaml: externalEndpoints.image" \
  "charts/confidential-router-ui/values.yaml: image" \
  "apps/confidential-router/app.yaml: components[router-api].images[0]" \
  "every first-party pin traces to an attested commit"

# A tag where the digest belongs. This is what a chart looks like mid-re-pin: the
# digest emptied, the tag filled in, and every other check in this repository
# still green — the goldens set their own digests and `helm lint` has no opinion.
case_run tag-not-digest 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "digest is empty — what is pinned is the tag main-82769ff"

# The same mistake made by deleting the key rather than emptying it. Nothing
# downstream distinguishes the two, so neither does this.
case_run first-party-tag-only 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "is first-party and declares no digest"

# A digest nothing attests. The realistic cause is a digest copied from a local
# build or from a registry the release workflow never pushed to; the failure has
# to name the digest, because the next question is always "which one".
case_run no-attestation 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd" \
  "attestation verification failed"

# The half-rebased train: the API re-pinned, the console left on the revision
# before it. Both digests are real, both attestations verify, and the listing
# deploys two halves of one contract built from two different commits.
case_run split-train 1 \
  "pins from Super-Protocol/confidential-router attest to 2 different commits" \
  "82769ff3fc764ab9c6e176280874175a4e5c09b4" \
  "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" \
  "charts/confidential-router-ui/values.yaml: image"

# The commit in the SLSA predicate is set by the workflow being attested; the one
# in the signing certificate comes from the OIDC token and is not the workflow's
# to write. Reading only the predicate would make this pass.
case_run forged-predicate 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "the signing certificate attests commit 82769ff3fc764ab9c6e176280874175a4e5c09b4"

# An attestation whose signing certificate names no source revision. The commit in
# the predicate is there and is right — reading that one and calling it provenance
# is the shortcut this refuses, so the verdict is "could not tell" rather than a
# commit taken from the field the attested workflow writes.
case_run no-source-commit 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "signing certificate names no source commit"

# An attestation that verifies, for a different image than the one pinned. `gh`
# rejects this itself today; the check stays because the bundle a future offline
# or registry-fetched verification hands back is not guaranteed to.
case_run subject-mismatch 1 \
  "charts/confidential-router-api/values.yaml: image" \
  "attestation subject is sha256:dddd"

if [ "${1:-}" = "--live" ]; then
  echo
  echo "verify-pins live, against $root"
  python3 "$verifier" --root "$root" || failures=$((failures + 1))
fi

echo
if [ "$failures" != 0 ]; then
  echo "verify-pins self-test: $failures failure(s)"
  exit 1
fi
echo "verify-pins self-test: every case behaves as it should."
