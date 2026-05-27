#!/usr/bin/env bash
# deploy/nats/bootstrap-streams.sh — idempotent JetStream stream provisioning
#
# Ensures lyra-events + lyra-metrics streams exist with the retention policy
# defined in bootstrap_streams.py. Safe to re-run.
#
# Usage (from repo root, after lyra-nats is running):
#   ./deploy/nats/bootstrap-streams.sh
#
# Requires: NATS container running on localhost:4222, hub.seed present.

set -euo pipefail

LYRA_DIR=$(cd "$(dirname "$0")/../.." && pwd)
NATS_URL="${NATS_URL:-nats://127.0.0.1:4222}"
NKEY_PATH="${NATS_NKEY_SEED_PATH:-${HOME}/.lyra/nkeys/hub.seed}"

info()  { echo "[+] $1"; }
error() { echo "[x] $1" >&2; exit 1; }

# ── Preconditions ───────────────────────────────────────────────────────────

if [[ ! -f "$NKEY_PATH" ]]; then
  error "NKey seed not found: $NKEY_PATH (run: make nats-setup)"
fi

# Extract host:port from NATS_URL (default nats://127.0.0.1:4222)
NATS_HOST_PORT="${NATS_URL#nats://}"
NATS_HOST="${NATS_HOST_PORT%:*}"
NATS_PORT="${NATS_HOST_PORT##*:}"

# Wait for NATS to accept connections (up to 30 s)
for _ in $(seq 30); do
  if nc -z "$NATS_HOST" "$NATS_PORT" 2>/dev/null; then
    break
  fi
  sleep 1
done
nc -z "$NATS_HOST" "$NATS_PORT" 2>/dev/null || error "NATS not reachable on ${NATS_HOST}:${NATS_PORT} — start lyra-nats first"

# ── Provision ───────────────────────────────────────────────────────────────

info "Provisioning JetStream streams ..."
export NATS_URL
export NATS_NKEY_SEED_PATH="$NKEY_PATH"
cd "$LYRA_DIR"
uv run python deploy/nats/bootstrap_streams.py

info "Done."
