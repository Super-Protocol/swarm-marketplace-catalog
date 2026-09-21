#!/usr/bin/env bash
# Seed marketplace listings that do not need extra environment variables.
#
# On the marketplace VM:
#   /opt/swarm-marketplace-catalog/scripts/seed-marketplace.sh
#
# From a laptop (rsync + remote seed):
#   MARKETPLACE_SSH=ubuntu@20.9.53.24 ./scripts/seed-marketplace.sh
#
# Override compose dir / catalog with MARKETPLACE_COMPOSE_DIR and SWM_SEED_CATALOG.

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
CATALOG_ROOT=$(cd "$HERE/.." && pwd)
FILTER="$HERE/filter-catalog.py"

COMPOSE_DIR=${MARKETPLACE_COMPOSE_DIR:-/etc/marketplace}
REMOTE_CATALOG=${REMOTE_CATALOG:-/opt/swarm-marketplace-catalog}
SSH_TARGET=${MARKETPLACE_SSH:-}

usage() {
  cat <<'EOF'
Seed applications and datasets that do not interpolate ${ENV} and whose
definitions do not declare publisher secrets.

Usage:
  seed-marketplace.sh              # on the marketplace VM
  MARKETPLACE_SSH=ubuntu@HOST seed-marketplace.sh   # from a laptop
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

run_seed_local() {
  local catalog=$1
  local work
  work=$(mktemp -d)
  trap 'rm -rf "$work"' RETURN
  rsync -a --delete --exclude '.git' --exclude 'charts' --exclude 'node_modules' "$catalog"/ "$work"/
  python3 "$FILTER" "$work"
  echo "[seed] compose dir $COMPOSE_DIR, catalog $work"
  sudo docker compose --project-directory "$COMPOSE_DIR" --env-file "$COMPOSE_DIR/.env" \
    --profile seed run --no-deps --rm \
    -e SWM_SEED_CATALOG=/catalog \
    -v "$work":/catalog:ro \
    seed
}

if [[ -z "$SSH_TARGET" && -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  run_seed_local "${SWM_SEED_CATALOG:-$CATALOG_ROOT}"
  exit 0
fi

if [[ -z "$SSH_TARGET" ]]; then
  echo "Set MARKETPLACE_SSH=ubuntu@<marketplace-ip> (or run this script on the VM)." >&2
  exit 1
fi

echo "[seed] syncing catalog to $SSH_TARGET:$REMOTE_CATALOG"
ssh -o StrictHostKeyChecking=accept-new "$SSH_TARGET" "sudo mkdir -p '$REMOTE_CATALOG' && sudo chown \$(id -un):\$(id -gn) '$REMOTE_CATALOG'"
rsync -az --delete --exclude '.git' --exclude 'charts' --exclude 'node_modules' \
  "$CATALOG_ROOT"/ "$SSH_TARGET:$REMOTE_CATALOG/"

ssh "$SSH_TARGET" "REMOTE_CATALOG='$REMOTE_CATALOG' MARKETPLACE_COMPOSE_DIR='$COMPOSE_DIR' bash '$REMOTE_CATALOG/scripts/seed-marketplace.sh'"
