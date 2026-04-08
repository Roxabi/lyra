#!/usr/bin/env bash
# Generate nkey seeds for NATS authentication
#
# Seeds (private keys) → ~/.lyra/nkeys/     owned by LYRA_USER, 0600 — no system access needed
# auth.conf (public keys) → /etc/nats/nkeys/ owned by root:nats,  0640 — read by nats-server
#
# Creates 5 user nkey seeds: hub, llm-worker, monitor, tts-adapter, stt-adapter
#
# Usage: sudo ./deploy/nats/gen-nkeys.sh
#        sudo ./deploy/nats/gen-nkeys.sh --fix-perms   # re-apply permissions without regenerating
#        sudo ./deploy/nats/gen-nkeys.sh --show        # print existing public keys
#
# Idempotent — skips if auth.conf already exists. Delete auth.conf + seeds dir to regenerate.
# Override seeds location: SEEDS_DIR=/custom/path sudo ./gen-nkeys.sh

set -euo pipefail

LYRA_USER="${SUDO_USER:-$(id -un)}"
LYRA_HOME=$(getent passwd "$LYRA_USER" | cut -d: -f6)
SEEDS_DIR="${SEEDS_DIR:-${LYRA_HOME}/.lyra/nkeys}"
AUTH_DIR="/etc/nats/nkeys"
AUTH_CONF="${AUTH_DIR}/auth.conf"
NK_BIN=""

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1" >&2; }
warn()  { echo -e "${YELLOW}[!]${NC} $1" >&2; }
error() { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }

SHOW_ONLY=false
FIX_PERMS=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --show) SHOW_ONLY=true; shift ;;
    --fix-perms) FIX_PERMS=true; shift ;;
    *) error "Unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || error "Must be run as root (sudo ./deploy/nats/gen-nkeys.sh)"

# ── show mode ──────────────────────────────────────────────────────────────
if [ "${SHOW_ONLY}" = true ]; then
  [ -f "${AUTH_CONF}" ] || error "auth.conf not found at ${AUTH_CONF} — run without --show first"
  echo "Public keys (${AUTH_CONF}):"
  grep -E 'nkey:|name:' "${AUTH_CONF}"
  echo ""
  echo "Seed files (${SEEDS_DIR}):"
  if [ -d "${SEEDS_DIR}" ]; then
    ls -la "${SEEDS_DIR}/"*.seed 2>/dev/null || echo "  (no .seed files found)"
  else
    echo "  (directory not found)"
  fi
  exit 0
fi

# ── apply permissions ──────────────────────────────────────────────────────
apply_permissions() {
  # Seeds — user-space, owned by LYRA_USER
  mkdir -p "${SEEDS_DIR}"
  chown "${LYRA_USER}:${LYRA_USER}" "${SEEDS_DIR}"
  chmod 0700 "${SEEDS_DIR}"
  for seed in hub llm-worker monitor tts-adapter stt-adapter; do
    if [ -f "${SEEDS_DIR}/${seed}.seed" ]; then
      chown "${LYRA_USER}:${LYRA_USER}" "${SEEDS_DIR}/${seed}.seed"
      chmod 0600 "${SEEDS_DIR}/${seed}.seed"
    fi
  done
  # auth.conf — system, readable by nats-server (nats user)
  if [ -f "${AUTH_CONF}" ]; then
    chown root:nats "${AUTH_CONF}"
    chmod 0640 "${AUTH_CONF}"
  fi
  info "Permissions applied for LYRA_USER=${LYRA_USER}, SEEDS_DIR=${SEEDS_DIR}"
}

# ── fix-perms mode ─────────────────────────────────────────────────────────
if [ "${FIX_PERMS}" = true ]; then
  [ -d "${SEEDS_DIR}" ] || error "${SEEDS_DIR} does not exist — run without --fix-perms first"
  apply_permissions
  exit 0
fi

# ── idempotency check ──────────────────────────────────────────────────────
if [ -f "${AUTH_CONF}" ]; then
  warn "auth.conf already exists — skipping. Remove ${AUTH_CONF} + ${SEEDS_DIR}/ to regenerate."
  warn "To re-apply permissions only: sudo ./deploy/nats/gen-nkeys.sh --fix-perms"
  exit 0
fi

# ── locate or download nk ──────────────────────────────────────────────────

ensure_nk() {
  if command -v nk &>/dev/null; then
    echo "nk"; return
  fi

  info "nk not found — downloading from GitHub releases..."

  local arch
  arch=$(dpkg --print-architecture 2>/dev/null \
    || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')

  local tmpdir
  tmpdir=$(mktemp -d)
  trap 'rm -rf "${tmpdir}"' EXIT

  # Resolve download URL directly from GitHub API assets (avoids guessing filename pattern)
  local release_json url version
  release_json=$(curl -fsSL --connect-timeout 10 \
    "https://api.github.com/repos/nats-io/nkeys/releases/latest" 2>/dev/null) || release_json=""

  if [ -n "${release_json}" ]; then
    url=$(echo "${release_json}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
arch = '${arch}'
for asset in data.get('assets', []):
    n = asset['name'].lower()
    if 'nk' in n and 'linux' in n and arch in n and n.endswith('.zip'):
        print(asset['browser_download_url'])
        break
" 2>/dev/null) || url=""
    version=$(echo "${release_json}" | python3 -c "
import sys, json; print(json.load(sys.stdin).get('tag_name','v0.4.6').lstrip('v'))
" 2>/dev/null) || version="0.4.6"
  fi

  # Fall back to known-good release if API didn't yield an asset URL
  if [ -z "${url}" ]; then
    version="0.4.6"
    url="https://github.com/nats-io/nkeys/releases/download/v${version}/nk-v${version}-linux-${arch}.zip"
    warn "Could not resolve asset URL from API — falling back to v${version}"
  fi

  info "Downloading nk v${version}..."
  curl -fsSL "${url}" -o "${tmpdir}/nk.zip" \
    || error "Failed to download nk v${version} from ${url}"

  if command -v unzip &>/dev/null; then
    unzip -q "${tmpdir}/nk.zip" -d "${tmpdir}"
  else
    python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
      "${tmpdir}/nk.zip" "${tmpdir}" \
      || error "Failed to extract nk.zip (no unzip and python3 extraction failed)"
  fi

  # Binary may be at root or inside a subdirectory
  local nk_bin
  nk_bin=$(find "${tmpdir}" -maxdepth 2 -name "nk" -type f | head -1)
  [ -n "${nk_bin}" ] || error "nk binary not found inside downloaded archive"
  chmod +x "${nk_bin}"

  # Verify SHA-256 when checksum file is available
  local sha_url
  sha_url="$(dirname "${url}")/SHA256SUMS"
  if curl -fsSL --connect-timeout 5 "${sha_url}" -o "${tmpdir}/SHA256SUMS" 2>/dev/null; then
    (cd "$(dirname "${nk_bin}")" \
      && grep -F "$(basename "${url}")" "${tmpdir}/SHA256SUMS" | sha256sum --check) \
      || warn "SHA-256 check failed — verify manually before trusting this binary"
  else
    warn "SHA256SUMS not available for this release — skipping integrity check"
  fi

  cp "${nk_bin}" /usr/local/bin/nk
  info "nk v${version} installed to /usr/local/bin/nk"
  echo "/usr/local/bin/nk"
}

NK_BIN=$(ensure_nk)

# ── create directories ─────────────────────────────────────────────────────
apply_permissions   # creates SEEDS_DIR with correct ownership before writing seeds
mkdir -p "${AUTH_DIR}"
chown root:nats "${AUTH_DIR}"
chmod 750 "${AUTH_DIR}"

# ── generate nkey pairs ────────────────────────────────────────────────────

generate_nkey() {
  local name="$1"
  local seed_file="${SEEDS_DIR}/${name}.seed"
  local tmp_seed
  tmp_seed=$(mktemp)
  trap 'rm -f "${tmp_seed}"' RETURN

  "${NK_BIN}" -gen user > "${tmp_seed}"
  local pubkey
  pubkey=$("${NK_BIN}" -inkey "${tmp_seed}" -pubout) \
    || error "Failed to derive public key for ${name} — is the nk binary valid?"

  install -m 0600 -o "${LYRA_USER}" -g "${LYRA_USER}" "${tmp_seed}" "${seed_file}"
  echo "${pubkey}"
}

info "Generating nkey pairs in ${SEEDS_DIR}/ ..."
HUB_PUB=$(generate_nkey "hub")
WORKER_PUB=$(generate_nkey "llm-worker")
MONITOR_PUB=$(generate_nkey "monitor")
TTS_PUB=$(generate_nkey "tts-adapter")
STT_PUB=$(generate_nkey "stt-adapter")

# ── write auth.conf (public keys only — safe for /etc/) ───────────────────

cat > "${AUTH_CONF}" << EOF
# NATS nkey authorization — generated by gen-nkeys.sh
# Included by /etc/nats/nats.conf
# DO NOT edit manually — regenerate with: sudo ./deploy/nats/gen-nkeys.sh
# Seeds (private keys) live in: ${SEEDS_DIR}/

authorization {
  users: [
    { nkey: "${HUB_PUB}" }          # hub
    { nkey: "${WORKER_PUB}" }       # llm-worker
    { nkey: "${MONITOR_PUB}" }      # monitor
    { nkey: "${TTS_PUB}" }          # tts-adapter
    { nkey: "${STT_PUB}" }          # stt-adapter
  ]
}
EOF
chown root:nats "${AUTH_CONF}"
chmod 0640 "${AUTH_CONF}"

info "Done."
info "  Seeds:     ${SEEDS_DIR}/"
info "  auth.conf: ${AUTH_CONF}"
info ""
info "  hub.seed          NATS_NKEY_SEED_PATH=${SEEDS_DIR}/hub.seed"
info "  llm-worker.seed   NATS_NKEY_SEED_PATH=${SEEDS_DIR}/llm-worker.seed"
info "  monitor.seed      NATS_NKEY_SEED_PATH=${SEEDS_DIR}/monitor.seed"
info "  tts-adapter.seed  NATS_NKEY_SEED_PATH=${SEEDS_DIR}/tts-adapter.seed"
info "  stt-adapter.seed  NATS_NKEY_SEED_PATH=${SEEDS_DIR}/stt-adapter.seed"
warn "Supervisor confs already reference ~/.lyra/nkeys/ — no changes needed."
