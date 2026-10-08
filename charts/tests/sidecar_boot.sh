#!/usr/bin/env bash
#
# Boots the egress sidecar image the confidential-router listing declares against
# the document router-api renders before any external endpoint is registered,
# and fails unless it is still running a few seconds later.
#
#   charts/tests/sidecar_boot.sh
#
# Every fresh deployment starts from exactly that document — `endpoints: []` —
# and an administrator can only register an endpoint through the API in the same
# pod. A sidecar that exits on it keeps the pod un-Ready, so the API Service has
# no endpoints and ingress-nginx answers 503 on every path: the only way out is
# the API the 503 is in front of. Listing 0.13.0 shipped that build (SUP-248);
# nothing in the goldens could see it, because it is a property of the binary
# behind the digest, not of anything rendered.
#
# The container is run the way the chart runs it: the image's own non-root uid
# with the API's group, a read-only root filesystem, and the config directory
# mounted read-only.
#
# Wants docker and network access to ghcr.io; not part of run.sh for that reason.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

repository=ghcr.io/super-protocol/confidential-router/gatekeeper
digest=$(python3 - "$repository" <<'PYTHON'
import sys, yaml
repository = sys.argv[1]
with open("apps/confidential-router/app.yaml") as f:
    listing = yaml.safe_load(f)
digests = {i["digest"] for c in listing["components"] for i in c.get("images", []) if i["name"] == repository}
if len(digests) != 1:
    sys.exit(f"apps/confidential-router/app.yaml declares {len(digests)} digests for {repository}, expected one")
print(digests.pop())
PYTHON
)
image="$repository@$digest"
echo "sidecar: $image"

# The container reads the file as uid 65532 through the shared group, so the
# directory has to be traversable and the file group-readable — the same 0640
# router-api writes.
work=$(mktemp -d)
name="sidecar-boot-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
chmod 0755 "$work"

# What `renderSidecarConfig` in router-api produces with no rows and no trusted
# measurements, at the chart's defaults (adminPort 9465, reattestInterval 10m).
cat >"$work/config.yaml" <<'YAML'
# Rendered by router-api from external_endpoints and trusted_measurements.
# Do not edit: every admin mutation overwrites this file (ADR-008 §5).
version: 1
attestedRoots:
  enabled: true
  trustedMeasurements: []
defaults:
  failMode: closed
  reattestInterval: 10m
  verdictCacheTtl: 60s
  maxBundleAge: 24h
admin:
  listen: 127.0.0.1:9465
endpoints: []
YAML
chmod 0644 "$work/config.yaml"

docker pull -q "$image" >/dev/null
docker run -d --name "$name" \
  --read-only \
  --user 65532:1001 \
  -v "$work:/var/run/gatekeeper:ro" \
  -e GATEKEEPER_CONFIG=/var/run/gatekeeper/config.yaml \
  -e GATEKEEPER_WATCH_INTERVAL=1s \
  "$image" >/dev/null

# Several watch intervals: long enough for a supervisor that hands the file to
# `gatekeeper run` to have done so and exited.
sleep "${SIDECAR_BOOT_WAIT:-8}"

state=$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$name")
logs=$(docker logs "$name" 2>&1 || true)
if [[ "$state" != running* ]]; then
  echo "FAIL: the sidecar is not running on an empty endpoint list (status/exit: $state)"
  echo "$logs" | sed 's/^/  /'
  exit 1
fi
echo "ok: the sidecar waits on an empty endpoint list instead of exiting"
echo "$logs" | tail -n 3 | sed 's/^/  /'
