#!/usr/bin/env bash
# Deploy NATS server-side nkey enforcement to Machine 1
#
# Run from the repo root:
#   make nats-deploy
#   (or) sudo ./deploy/nats/nats-deploy.sh
#
# What it does:
#   1. Force-installs deploy/nats/nats.conf → /etc/nats/nats.conf
#      (replaces any existing file, including the old nats-local.conf content)
#   2. Runs gen-nkeys.sh to generate /etc/nats/nkeys/auth.conf if not present
#   3. Restarts nats.service to apply the new config (NATS does not support live reload)
#
# Safe to re-run — gen-nkeys.sh is idempotent (skips if auth.conf exists).
# To rotate keys: delete /etc/nats/nkeys/ then re-run.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }
section() { echo -e "\n${GREEN}=== $1 ===${NC}"; }

[ "$(id -u)" -eq 0 ] || error "Must be run as root (sudo ./deploy/nats/nats-deploy.sh)"

LYRA_DIR=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
NATS_CONF_SRC="${LYRA_DIR}/deploy/nats/nats.conf"
NATS_CONF_DST="/etc/nats/nats.conf"
NKEYS_AUTH="/etc/nats/nkeys/auth.conf"

# ── 1. Install / update nats.conf ──────────────────────────────────────────

section "NATS config"

[ -f "${NATS_CONF_SRC}" ] || error "Source config not found: ${NATS_CONF_SRC}"

if [ -f "${NATS_CONF_DST}" ]; then
  if diff -q "${NATS_CONF_SRC}" "${NATS_CONF_DST}" &>/dev/null; then
    info "nats.conf already up to date — no change needed."
  else
    install -m 644 -o root -g nats "${NATS_CONF_SRC}" "${NATS_CONF_DST}"
    info "nats.conf updated at ${NATS_CONF_DST}."
  fi
else
  # Ensure directory + nats group exist (install.sh may not have run)
  mkdir -p /etc/nats
  id nats &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin --comment "NATS Server" nats
  chown root:nats /etc/nats
  chmod 750 /etc/nats
  install -m 644 -o root -g nats "${NATS_CONF_SRC}" "${NATS_CONF_DST}"
  info "nats.conf installed at ${NATS_CONF_DST}."
fi

# ── 2. Generate nkeys (idempotent) ─────────────────────────────────────────

section "nkey generation"

if [ -f "${NKEYS_AUTH}" ]; then
  info "auth.conf already exists — skipping key generation."
  info "To rotate keys: sudo rm -rf /etc/nats/nkeys && re-run."
else
  info "Generating nkeys..."
  bash "${LYRA_DIR}/deploy/nats/gen-nkeys.sh"
fi

# Gate: abort if auth.conf is still missing — nats.conf includes it; NATS won't start without it
[ -f "${NKEYS_AUTH}" ] || error "Key generation failed — auth.conf missing, aborting before service restart"

# ── 3. Restart NATS ────────────────────────────────────────────────────────
#
# NATS does not support live config reload via SIGHUP (SIGHUP triggers graceful shutdown,
# not a config re-read). Always do a full restart to apply the new config.

section "NATS service"

if systemctl is-active --quiet nats.service; then
  systemctl restart nats.service
  info "nats.service restarted."
else
  warn "nats.service is not running — starting..."
  systemctl start nats.service
  info "nats.service started."
fi

# Wait for NATS to accept connections (port 4222, max 5s)
for i in $(seq 10); do
  nc -z 127.0.0.1 4222 2>/dev/null && break
  sleep 0.5
done
systemctl is-active --quiet nats.service \
  && info "nats.service is running." \
  || error "nats.service failed to start — check: journalctl -u nats.service -n 50"

# ── 4. Quick connectivity check ────────────────────────────────────────────

section "Verification"

if command -v nats &>/dev/null; then
  # Unauthenticated connection should now be REJECTED.
  # Assert both: non-zero exit AND an auth error in the output.
  rc=0
  output=$(nats pub --server nats://127.0.0.1:4222 test.ping "" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ] && echo "$output" | grep -qiE "authoriz|permission|auth"; then
    info "Unauthenticated connections are rejected. nkey enforcement is ACTIVE."
  else
    error "nkey enforcement NOT confirmed — unauthenticated publish did not return an auth error (rc=$rc). Check: journalctl -u nats.service -n 20"
  fi
else
  warn "nats CLI not installed — skipping connectivity check."
  warn "Verify manually: nats sub '>' (should be rejected without --nkey)"
fi

section "Done"
info "nats.conf with nkey enforcement deployed."
info "Lyra hub + adapters must have NATS_NKEY_SEED_PATH set in their supervisor conf.d."
info "See: sudo ./deploy/nats/gen-nkeys.sh --show"
