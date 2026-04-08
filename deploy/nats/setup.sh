#!/usr/bin/env bash
# Lyra by Roxabi — NATS setup (install + configure + start)
#
# Usage: cd ~/projects/lyra && make nats-setup
#
# Does everything in one idempotent pass:
#   1. nats-server binary
#   2. nats system user + /etc/nats directories
#   3. nats.conf (install or update)
#   4. nats.service systemd unit + lyra.service ordering drop-in
#   5. UFW firewall rule (port 4222, LAN only)
#   6. TLS certs (gen-certs.sh — skips if present)
#   7. nkey seeds (gen-nkeys.sh — skips if present, re-applies permissions always)
#   8. Start / restart nats.service
#   9. Verify nkey enforcement is active
#
# Safe to re-run after upgrades, re-provisioning, or permission drift.
# To rotate keys: sudo rm -f /etc/nats/nkeys/auth.conf && rm -rf ~/.lyra/nkeys && make nats-setup

set -euo pipefail

[[ $EUID -eq 0 ]] && { echo "[!] Do not run as root — use: make nats-setup"; exit 1; }

export PATH="$HOME/.local/bin:$PATH"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
section() { echo -e "\n${GREEN}=== $1 ===${NC}"; }

NATS_VERSION="2.10.22"  # pinned — bump when upgrading
LYRA_DIR=$(cd "$(dirname "$0")/../.." && pwd)
NATS_CONF_SRC="${LYRA_DIR}/deploy/nats/nats.conf"
NATS_CONF_DST="/etc/nats/nats.conf"
NKEYS_AUTH="/etc/nats/nkeys/auth.conf"

# ── 1. Binary ─────────────────────────────────────────────────────────────

section "NATS server binary"
if [ -x /usr/local/bin/nats-server ]; then
  info "nats-server already installed ($(/usr/local/bin/nats-server --version 2>&1 | head -1))."
else
  ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  NATS_TARBALL="nats-server-v${NATS_VERSION}-linux-${ARCH}.tar.gz"
  NATS_URL="https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/${NATS_TARBALL}"
  NATS_SHA_URL="https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/SHA256SUMS"
  NATS_TMP=$(mktemp -d)
  trap 'rm -rf "${NATS_TMP}"' EXIT
  curl -fsSL "${NATS_URL}"     -o "${NATS_TMP}/${NATS_TARBALL}"
  curl -fsSL "${NATS_SHA_URL}" -o "${NATS_TMP}/SHA256SUMS"
  (cd "${NATS_TMP}" && grep -F "${NATS_TARBALL}" SHA256SUMS | sha256sum --check) \
    || error "SHA-256 verification failed for nats-server v${NATS_VERSION}"
  tar -xz -C "${NATS_TMP}" -f "${NATS_TMP}/${NATS_TARBALL}"
  sudo install -m 755 "${NATS_TMP}/nats-server-v${NATS_VERSION}-linux-${ARCH}/nats-server" \
    /usr/local/bin/nats-server
  info "nats-server v${NATS_VERSION} installed."
fi

# ── 2. System user + directories ─────────────────────────────────────────

section "NATS system user + directories"
if id nats &>/dev/null; then
  info "nats user already exists."
else
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin --comment "NATS Server" nats
  info "nats system user created."
fi
sudo mkdir -p /etc/nats/certs
sudo chown root:root /etc/nats /etc/nats/certs
sudo chmod 755 /etc/nats /etc/nats/certs

# ── 3. nats.conf ─────────────────────────────────────────────────────────

section "NATS config"
[ -f "${NATS_CONF_SRC}" ] || error "Source config not found: ${NATS_CONF_SRC}"
if [ -f "${NATS_CONF_DST}" ] && diff -q "${NATS_CONF_SRC}" "${NATS_CONF_DST}" &>/dev/null; then
  info "nats.conf already up to date."
else
  sudo install -m 644 -o root -g nats "${NATS_CONF_SRC}" "${NATS_CONF_DST}"
  info "nats.conf installed/updated."
fi

# ── 4. systemd unit + lyra.service drop-in ───────────────────────────────

section "systemd"
if [ ! -f /etc/systemd/system/nats.service ]; then
  sudo install -m 644 "${LYRA_DIR}/deploy/nats/nats.service" /etc/systemd/system/nats.service
  sudo systemctl daemon-reload
  sudo systemctl enable nats.service
  info "nats.service installed and enabled."
else
  info "nats.service already installed."
fi

# Clean up stale drop-in (user units can't depend on system units)
DROPIN_DIR="$HOME/.config/systemd/user/lyra.service.d"
DROPIN="${DROPIN_DIR}/after-nats.conf"
if [ -f "${DROPIN}" ]; then
  rm -f "${DROPIN}"
  rmdir "${DROPIN_DIR}" 2>/dev/null || true
  systemctl --user daemon-reload 2>/dev/null || true
  info "Removed stale lyra.service drop-in (After=nats.service)."
else
  info "No stale drop-in to clean."
fi

# ── 5. Firewall ───────────────────────────────────────────────────────────

section "Firewall"
if sudo ufw status | grep -q "4222"; then
  info "UFW NATS rule already exists."
else
  sudo ufw allow from 192.168.1.0/24 to any port 4222 proto tcp comment "NATS (LAN)"
  info "UFW: port 4222 allowed from 192.168.1.0/24."
fi

# ── 6. TLS certs ─────────────────────────────────────────────────────────

section "TLS certs"
if [ -f /etc/nats/certs/server.crt ] && [ -f /etc/nats/certs/server.key ]; then
  info "TLS certs already present."
else
  sudo "${LYRA_DIR}/deploy/nats/gen-certs.sh"
fi

# ── 7. nkeys ─────────────────────────────────────────────────────────────

section "nkeys"
if [ -f "${NKEYS_AUTH}" ]; then
  info "auth.conf already exists — skipping key generation."
  sudo "${LYRA_DIR}/deploy/nats/gen-nkeys.sh" --fix-perms
else
  sudo "${LYRA_DIR}/deploy/nats/gen-nkeys.sh"
fi
sudo test -f "${NKEYS_AUTH}" || error "Key generation failed — auth.conf missing"

# ── 8. Start / restart ───────────────────────────────────────────────────

section "NATS service"
if sudo systemctl is-active --quiet nats.service; then
  sudo systemctl restart nats.service
  info "nats.service restarted."
else
  sudo systemctl start nats.service
  info "nats.service started."
fi

for _ in $(seq 20); do
  nc -z 127.0.0.1 4222 2>/dev/null && break
  sleep 0.5
done
sudo systemctl is-active --quiet nats.service \
  || error "nats.service failed to start — check: journalctl -u nats.service -n 50"
info "nats.service is running."

# ── 9. Verify ─────────────────────────────────────────────────────────────

section "Verification"
if command -v nats &>/dev/null; then
  rc=0
  output=$(nats pub --server nats://127.0.0.1:4222 test.ping "" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ] && echo "$output" | grep -qiE "authoriz|permission|auth"; then
    info "Unauthenticated connections rejected — nkey enforcement ACTIVE."
  else
    error "nkey enforcement NOT confirmed (rc=$rc). Check: journalctl -u nats.service -n 20"
  fi
else
  warn "nats CLI not installed — skipping. Verify manually: nats sub '>' (should fail without nkey)"
fi

section "Done"

# ── 10. Wire NATS env vars into .env ─────────────────────────────────────
LYRA_USER="${SUDO_USER:-$(id -un)}"
LYRA_HOME=$(getent passwd "$LYRA_USER" | cut -d: -f6)
ENV_FILE="${LYRA_DIR}/.env"
HUB_SEED="${LYRA_HOME}/.lyra/nkeys/hub.seed"
NATS_CA="/etc/nats/certs/ca.crt"
if [ -f "${ENV_FILE}" ]; then
  # NATS_URL — use tls:// scheme for TLS-enabled server
  if grep -q "^NATS_URL=" "${ENV_FILE}"; then
    info ".env already has NATS_URL — not overwriting."
  else
    echo "NATS_URL=tls://127.0.0.1:4222" >> "${ENV_FILE}"
    info "NATS_URL=tls://127.0.0.1:4222 added to .env."
  fi
  # NATS_NKEY_SEED_PATH — nkey authentication
  if grep -q "^NATS_NKEY_SEED_PATH=" "${ENV_FILE}"; then
    info ".env already has NATS_NKEY_SEED_PATH — not overwriting."
  else
    echo "NATS_NKEY_SEED_PATH=${HUB_SEED}" >> "${ENV_FILE}"
    info "NATS_NKEY_SEED_PATH=${HUB_SEED} added to .env."
  fi
  # NATS_CA_CERT — CA certificate for TLS verification
  if grep -q "^NATS_CA_CERT=" "${ENV_FILE}"; then
    info ".env already has NATS_CA_CERT — not overwriting."
  else
    echo "NATS_CA_CERT=${NATS_CA}" >> "${ENV_FILE}"
    info "NATS_CA_CERT=${NATS_CA} added to .env."
  fi
else
  warn ".env not found at ${ENV_FILE} — add manually:"
  warn "  NATS_URL=tls://127.0.0.1:4222"
  warn "  NATS_NKEY_SEED_PATH=${HUB_SEED}"
  warn "  NATS_CA_CERT=${NATS_CA}"
fi

info "NATS setup complete."
sudo "${LYRA_DIR}/deploy/nats/gen-nkeys.sh" --show
