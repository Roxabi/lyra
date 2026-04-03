#!/usr/bin/env bash
# Lyra by Roxabi — NATS server opt-in provisioning
# Usage: cd ~/projects/lyra && make nats-install
#
# Installs the NATS server binary, system user, config, systemd unit,
# and lyra.service ordering drop-in. Idempotent — safe to run multiple times.
#
# Run this AFTER provision.sh on machines that participate in multi-machine
# NATS pub/sub (Machine 1 hub and any compute workers).
set -euo pipefail

[[ $EUID -eq 0 ]] && { echo "[!] Do not run as root — use: make nats-install"; exit 1; }

export PATH="$HOME/.local/bin:$PATH"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
section() { echo -e "\n${GREEN}=== $1 ===${NC}"; }

NATS_VERSION="2.10.22"  # pinned — update when upgrading
LYRA_DIR=$(cd "$(dirname "$0")/../.." && pwd)

section "NATS server binary"
if [ -x /usr/local/bin/nats-server ]; then
  info "nats-server already installed ($(/usr/local/bin/nats-server --version 2>&1 | head -1))."
else
  ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  NATS_TARBALL="nats-server-v${NATS_VERSION}-linux-${ARCH}.tar.gz"
  NATS_URL="https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/${NATS_TARBALL}"
  NATS_SHA256_URL="https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/SHA256SUMS"
  NATS_TMP=$(mktemp -d)
  trap 'rm -rf "${NATS_TMP}"' EXIT
  curl -fsSL "${NATS_URL}" -o "${NATS_TMP}/${NATS_TARBALL}"
  curl -fsSL "${NATS_SHA256_URL}" -o "${NATS_TMP}/SHA256SUMS"
  (cd "${NATS_TMP}" && grep -F "${NATS_TARBALL}" SHA256SUMS | sha256sum --check) \
    || error "SHA-256 verification failed for nats-server v${NATS_VERSION}"
  tar -xz -C "${NATS_TMP}" -f "${NATS_TMP}/${NATS_TARBALL}"
  sudo install -m 755 "${NATS_TMP}/nats-server-v${NATS_VERSION}-linux-${ARCH}/nats-server" /usr/local/bin/nats-server
  info "nats-server v${NATS_VERSION} installed to /usr/local/bin/nats-server."
fi

section "NATS system user + directories"
if id nats &>/dev/null; then
  info "nats user already exists."
else
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin --comment "NATS Server" nats
  info "nats system user created."
fi
sudo mkdir -p /etc/nats/certs /etc/nats/nkeys
sudo chown -R root:nats /etc/nats
sudo chmod 750 /etc/nats /etc/nats/certs /etc/nats/nkeys

section "NATS config + systemd unit"
if [ -f /etc/nats/nats.conf ]; then
  info "nats.conf already installed."
else
  if [ -f "${LYRA_DIR}/deploy/nats/nats.conf" ]; then
    sudo install -m 644 -o root -g nats "${LYRA_DIR}/deploy/nats/nats.conf" /etc/nats/nats.conf
    info "nats.conf installed to /etc/nats/nats.conf."
  else
    warn "lyra not found at ${LYRA_DIR} — skipping nats.conf install."
    warn "Run after cloning: sudo install -m 644 ${LYRA_DIR}/deploy/nats/nats.conf /etc/nats/nats.conf"
  fi
fi

if [ -f /etc/systemd/system/nats.service ]; then
  info "nats.service already installed."
else
  if [ -f "${LYRA_DIR}/deploy/nats/nats.service" ]; then
    sudo install -m 644 "${LYRA_DIR}/deploy/nats/nats.service" /etc/systemd/system/nats.service
    sudo systemctl daemon-reload
    sudo systemctl enable nats.service
    info "nats.service installed and enabled."
  else
    warn "lyra not found at ${LYRA_DIR} — skipping nats.service install."
    warn "Run after cloning: sudo install ${LYRA_DIR}/deploy/nats/nats.service /etc/systemd/system/"
  fi
fi

section "lyra.service ordering (After=nats.service)"
# User units cannot directly depend on system units; drop-in is the correct mechanism.
NATS_DROPIN_DIR="$HOME/.config/systemd/user/lyra.service.d"
NATS_DROPIN="${NATS_DROPIN_DIR}/after-nats.conf"
if [ -f "${NATS_DROPIN}" ]; then
  info "lyra.service After=nats.service drop-in already installed."
else
  mkdir -p "${NATS_DROPIN_DIR}"
  cat > "${NATS_DROPIN}" << 'DROPIN'
[Unit]
After=nats.service
DROPIN
  systemctl --user daemon-reload 2>/dev/null || true
  info "lyra.service will now start after nats.service (drop-in installed)."
fi

section "Firewall (NATS port 4222 — LAN only)"
if sudo ufw status | grep -q "4222"; then
  info "UFW NATS rule already exists."
else
  sudo ufw allow from 192.168.1.0/24 to any port 4222 proto tcp comment "NATS (LAN)"
  info "UFW: port 4222 allowed from 192.168.1.0/24."
fi

section "Done — NATS opt-in provisioning complete"
info "Next steps:"
echo "  1. Generate TLS certs + nkeys:"
echo ""
echo "     sudo ${LYRA_DIR}/deploy/nats/gen-certs.sh"
echo "     sudo ${LYRA_DIR}/deploy/nats/gen-nkeys.sh"
echo ""
echo "  2. Start NATS:"
echo ""
echo "     sudo systemctl start nats.service"
echo "     sudo systemctl status nats.service"
echo ""
echo "  3. Restart lyra to activate new After=nats.service ordering:"
echo ""
echo "     systemctl --user restart lyra.service"
