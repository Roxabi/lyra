#!/usr/bin/env bash
# Generate nkey seeds for NATS authentication
#
# Seeds (private keys) → ~/.lyra/nkeys/     owned by LYRA_USER, 0600 — no system access needed
# auth.conf (public keys) → /etc/nats/nkeys/ owned by root:nats,  0640 — read by nats-server
#
# Creates 10 user nkey seeds: hub, telegram-adapter, discord-adapter,
#                              tts-adapter, stt-adapter, voice-tts, voice-stt,
#                              llm-worker, image-worker, monitor
#
# ACL matrix (identities + publish/subscribe allow-lists) is sourced from
# deploy/nats/acl-matrix.json — do not edit inline; update the JSON instead.
# Requires jq >= 1.6 on $PATH.
#
# Usage: sudo ./deploy/nats/gen-nkeys.sh
#        sudo ./deploy/nats/gen-nkeys.sh --fix-perms        # re-apply permissions without regenerating
#        sudo ./deploy/nats/gen-nkeys.sh --show             # print existing public keys
#        sudo ./deploy/nats/gen-nkeys.sh --regenerate       # atomic backup + wipe + regenerate (rotates keys)
#        sudo ./deploy/nats/gen-nkeys.sh --regenerate --yes # non-interactive regenerate
#        sudo ./deploy/nats/gen-nkeys.sh --regen-authconf   # re-render auth.conf from EXISTING seeds (no key rotation)
#             ./deploy/nats/gen-nkeys.sh --template-only    # write auth.conf skeleton to stdout (no root needed)
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

MATRIX_JSON="$(dirname "${BASH_SOURCE[0]}")/acl-matrix.json"

# ── load_matrix ───────────────────────────────────────────────────────────────
# Reads deploy/nats/acl-matrix.json and populates PUB_ALLOW, SUB_ALLOW, IDENTITIES.
# Aborts with a clear error if jq is missing/too old, JSON is absent/malformed,
# .version != "1", or any identity has an invalid schema.
load_matrix() {
  # Assert jq on $PATH
  command -v jq >/dev/null 2>&1 \
    || error "jq is required (apt-get install -y jq) — not found on \$PATH"

  # Assert jq >= 1.6
  local jqver
  jqver=$(jq --version 2>/dev/null | sed 's/^jq-//')
  local jq_major jq_minor
  jq_major=$(echo "${jqver}" | cut -d. -f1)
  jq_minor=$(echo "${jqver}" | cut -d. -f2)
  if [ "${jq_major}" -lt 1 ] || { [ "${jq_major}" -eq 1 ] && [ "${jq_minor}" -lt 6 ]; }; then
    error "jq >= 1.6 required (found: ${jqver})"
  fi

  # Assert MATRIX_JSON exists
  [ -f "${MATRIX_JSON}" ] \
    || error "ACL matrix not found: ${MATRIX_JSON}"

  # Assert .version == "1"
  local v
  v=$(jq -r '.version' "${MATRIX_JSON}")
  [ "${v}" = "1" ] \
    || error "unsupported acl-matrix.json version: ${v} (expected \"1\")"

  # Validate all identities and populate arrays
  declare -gA PUB_ALLOW SUB_ALLOW
  IDENTITIES=()

  local valid_owners="lyra voicecli imagecli reserved"
  while IFS= read -r name; do
    IDENTITIES+=("${name}")

    for field in owner description publish subscribe; do
      local has
      has=$(jq -r --arg n "${name}" --arg f "${field}" \
        'if .identities[$n] | has($f) then "yes" else "no" end' "${MATRIX_JSON}")
      [ "${has}" = "yes" ] \
        || error "acl-matrix.json: identity '${name}' missing field '${field}'"
    done

    local o
    o=$(jq -r --arg n "${name}" '.identities[$n].owner' "${MATRIX_JSON}")
    echo " ${valid_owners} " | grep -qw "${o}" \
      || error "acl-matrix.json: identity '${name}' has invalid owner '${o}'"

    PUB_ALLOW[$name]=$(jq -r --arg n "${name}" \
      '.identities[$n].publish | map("\"" + . + "\"") | join(",")' "${MATRIX_JSON}")
    SUB_ALLOW[$name]=$(jq -r --arg n "${name}" \
      '.identities[$n].subscribe | map("\"" + . + "\"") | join(",")' "${MATRIX_JSON}")
  done < <(jq -r '.identities | keys_unsorted[]' "${MATRIX_JSON}")
}

# ── emit_user (T1.3) ──────────────────────────────────────────────────────────
# Writes one authorization users[] entry block to stdout.
# Args: name pubkey
emit_user() {
  local name="$1" pubkey="$2"
  cat <<USER
    {
      nkey: "${pubkey}"
      # ${name}
      permissions: {
        publish:   { allow: [${PUB_ALLOW[$name]:-}] }
        subscribe: { allow: [${SUB_ALLOW[$name]:-}] }
        allow_responses: true
      }
    }
USER
}

# ── flag parsing ───────────────────────────────────────────────────────────────
SHOW_ONLY=false
FIX_PERMS=false
TEMPLATE_ONLY=false
REGENERATE=false
REGEN_AUTHCONF=false
AUTO_YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --show)            SHOW_ONLY=true;      shift ;;
    --fix-perms)       FIX_PERMS=true;      shift ;;
    --template-only)   TEMPLATE_ONLY=true;  shift ;;
    --regenerate)      REGENERATE=true;     shift ;;
    --regen-authconf)  REGEN_AUTHCONF=true; shift ;;
    --yes)             AUTO_YES=true;       shift ;;
    *) error "Unknown option: $1" ;;
  esac
done

# ── load ACL matrix ───────────────────────────────────────────────────────────
load_matrix

# ── auth.conf renderer (shared by --template-only and regenerate paths) ──────
# Accepts the name of an associative array of pubkeys as $1. Writes the full
# `authorization { default_permissions { ... } users: [ ... ] }` block to stdout.
render_auth_conf() {
  local -n _pubkeys=$1  # nameref to caller's pubkey array
  echo "# NATS nkey authorization — generated by gen-nkeys.sh"
  echo "# Spec: artifacts/specs/706-per-role-nkeys-acls-spec.mdx"
  echo "# TODO(ADR-045): ACL for roxabi-nats SDK identities intentionally omitted"
  echo "#                until the SDK extraction lands — rerun this script then."
  echo "# DO NOT edit manually — regenerate with: sudo ./deploy/nats/gen-nkeys.sh --regenerate"
  echo "authorization {"
  # Defense-in-depth: any user without explicit permissions (e.g. someone adds a
  # user block manually and forgets the permissions{} section) falls back to
  # deny-all instead of NATS's implicit allow-all.
  echo "  default_permissions: {"
  echo "    publish:   { deny: [\">\"] }"
  echo "    subscribe: { deny: [\">\"] }"
  echo "  }"
  echo "  users: ["
  for name in "${IDENTITIES[@]}"; do
    emit_user "$name" "${_pubkeys[$name]}"
  done
  echo "  ]"
  echo "}"
}

# ── template-only mode (T1.4) — no root, no filesystem writes ─────────────────
if [ "${TEMPLATE_ONLY}" = true ]; then
  declare -A DUMMY_PUBKEYS
  for name in "${IDENTITIES[@]}"; do
    # Stable dummy pubkey per identity (uppercase name, no hyphens)
    local_dummy="UDUMMY${name//-/}"
    DUMMY_PUBKEYS[$name]="${local_dummy^^}"
  done
  render_auth_conf DUMMY_PUBKEYS
  exit 0
fi

# ── root check (required for all other modes) ──────────────────────────────────
[ "$(id -u)" -eq 0 ] || error "Must be run as root (sudo ./deploy/nats/gen-nkeys.sh)"

# ── show mode ──────────────────────────────────────────────────────────────────
if [ "${SHOW_ONLY}" = true ]; then
  [ -f "${AUTH_CONF}" ] || error "auth.conf not found at ${AUTH_CONF} — run without --show first"
  echo "Public keys (${AUTH_CONF}):"
  grep -E 'nkey:|# ' "${AUTH_CONF}"
  echo ""
  echo "Seed files (${SEEDS_DIR}):"
  if [ -d "${SEEDS_DIR}" ]; then
    ls -la "${SEEDS_DIR}/"*.seed 2>/dev/null || echo "  (no .seed files found)"
  else
    echo "  (directory not found)"
  fi
  exit 0
fi

# ── apply permissions ──────────────────────────────────────────────────────────
apply_permissions() {
  # Seeds — user-space, owned by LYRA_USER
  mkdir -p "${SEEDS_DIR}"
  chown "${LYRA_USER}:${LYRA_USER}" "${SEEDS_DIR}"
  chmod 0700 "${SEEDS_DIR}"
  # T1.5: extended to 7 identities; #689 adds voice-tts, voice-stt (9 total); #754 adds image-worker (10 total)
  # TODO(#717): drive from acl-matrix.json identities list — out of scope for this refactor
  for seed in hub telegram-adapter discord-adapter tts-adapter stt-adapter voice-tts voice-stt llm-worker image-worker monitor; do
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

# ── generate_nkey (defined early so --regen-authconf can call it) ─────────────
# Writes a fresh user nkey seed to ${SEEDS_DIR}/${name}.seed and echoes its
# public key. Relies on NK_BIN + LYRA_USER being set (both resolved at the
# top of the file or by ensure_nk). Used by default mode (all seeds) and
# --regen-authconf (only missing seeds).
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

# ── fix-perms mode ─────────────────────────────────────────────────────────────
if [ "${FIX_PERMS}" = true ]; then
  [ -d "${SEEDS_DIR}" ] || error "${SEEDS_DIR} does not exist — run without --fix-perms first"
  apply_permissions
  exit 0
fi

# ── regen-authconf mode: re-render auth.conf from existing seeds ──────────────
# Purpose: upgrade a live auth.conf to the current IDENTITIES + ACL matrix
# without rotating keys. Used when the script's ACL model has evolved
# (e.g. #714 added per-role ACLs, #689 added voice-{stt,tts} roles) but seeds
# on disk are still valid.
#
# Differs from --regenerate: no seed wipe, no key rotation — existing services
# keep working, only the auth.conf content changes.
if [ "${REGEN_AUTHCONF}" = true ]; then
  [ -d "${SEEDS_DIR}" ] || error "${SEEDS_DIR} does not exist — run without flags first to seed keys"

  # Need nk to derive pubkeys from existing seeds
  NK_BIN=$(command -v nk || echo "")
  [ -n "${NK_BIN}" ] || error "nk not found — run without flags first (it will install nk)"

  # Collect pubkeys — derive from existing seeds or create missing ones
  declare -A EXISTING_PUBKEYS
  for name in "${IDENTITIES[@]}"; do
    seed_file="${SEEDS_DIR}/${name}.seed"
    if [ -f "${seed_file}" ]; then
      pubkey=$("${NK_BIN}" -inkey "${seed_file}" -pubout 2>/dev/null) \
        || error "Failed to derive pubkey from ${seed_file}"
      info "Derived pubkey from existing seed: ${name}"
      EXISTING_PUBKEYS[$name]="${pubkey}"
    else
      info "Created missing seed: ${name}"
      EXISTING_PUBKEYS[$name]=$(generate_nkey "${name}")
    fi
  done

  # Backup current auth.conf if present
  if [ -f "${AUTH_CONF}" ]; then
    BACKUP_AUTH="${AUTH_CONF}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "${AUTH_CONF}" "${BACKUP_AUTH}"
    chmod 0640 "${BACKUP_AUTH}"
    info "Backed up auth.conf → ${BACKUP_AUTH}"
  fi

  # Render new auth.conf to temp, install atomically
  TMP_AUTH=$(mktemp)
  trap 'rm -f "${TMP_AUTH}"' EXIT
  render_auth_conf EXISTING_PUBKEYS > "${TMP_AUTH}"

  # Validate with nats-server -t if available
  if command -v nats-server &>/dev/null && [ -f /etc/nats/nats.conf ]; then
    # Temporarily stage: copy to AUTH_CONF location for include-path resolution,
    # keep backup as safety net.
    install -m 0640 -o root -g nats "${TMP_AUTH}" "${AUTH_CONF}"
    if ! nats-server -t -c /etc/nats/nats.conf >/dev/null 2>&1; then
      if [ -n "${BACKUP_AUTH:-}" ] && [ -f "${BACKUP_AUTH}" ]; then
        cp -a "${BACKUP_AUTH}" "${AUTH_CONF}"
        error "nats-server config validation failed — restored backup. Inspect ${TMP_AUTH} for issues."
      else
        rm -f "${AUTH_CONF}"
        error "nats-server config validation failed — no backup to restore. Re-run --regenerate if needed."
      fi
    fi
    info "nats-server config validation OK."
  else
    install -m 0640 -o root -g nats "${TMP_AUTH}" "${AUTH_CONF}"
    warn "nats-server not found or /etc/nats/nats.conf missing — skipped config validation."
  fi

  info "auth.conf re-rendered from ${#IDENTITIES[@]} existing seeds."
  info "Next: sudo systemctl reload nats.service"
  exit 0
fi

# ── regenerate mode: atomic backup + wipe (T1.7) ──────────────────────────────
if [ "${REGENERATE}" = true ]; then
  if [ "${AUTO_YES}" = false ]; then
    if [ -t 0 ]; then
      read -r -p "This will wipe ~/.lyra/nkeys/ and /etc/nats/nkeys/auth.conf (backups will be created). Continue? [y/N] " reply
      [[ "${reply}" =~ ^[Yy]$ ]] || { warn "Aborted."; exit 0; }
    else
      error "stdin is not a TTY — pass --yes to confirm non-interactive wipe"
    fi
  fi

  epoch=$(date +%s)
  BACKUP_AUTH="${AUTH_CONF}.bak.${epoch}"
  BACKUP_SEEDS="${SEEDS_DIR}.bak.${epoch}"

  # Atomic backup: both must succeed before any delete
  if [ -f "${AUTH_CONF}" ]; then
    cp -a "${AUTH_CONF}" "${BACKUP_AUTH}" \
      || error "Failed to back up ${AUTH_CONF} — aborting before any delete"
    # NB3: tighten backup perms (cp -a preserves source but not parent dir mode)
    chmod 0640 "${BACKUP_AUTH}"
    info "Backed up auth.conf → ${BACKUP_AUTH}"
  fi
  if [ -d "${SEEDS_DIR}" ]; then
    cp -a "${SEEDS_DIR}" "${BACKUP_SEEDS}" \
      || error "Failed to back up ${SEEDS_DIR} — aborting before any delete"
    # NB3: explicit 0700 on backup dir + 0600 on backed-up seeds
    chmod 0700 "${BACKUP_SEEDS}"
    find "${BACKUP_SEEDS}" -maxdepth 1 -name '*.seed' -exec chmod 0600 {} \;
    info "Backed up seeds → ${BACKUP_SEEDS}/"
  fi

  # B4: auto-restore trap — if any error occurs between the rm and the auth.conf
  # write below, put the backups back so the system never ends up with wiped
  # state + no new auth.conf. Trap is cleared on successful write at the end.
  # B7: first thing in the handler must be `trap - ERR INT TERM` — otherwise
  # the `exit 1` below re-triggers the ERR trap recursively (infinite loop on
  # bash with `set -e`).
  restore_backups() {
    local rc=$?
    trap - ERR INT TERM
    warn "Regeneration failed (exit ${rc}) — restoring backups..."
    if [ -f "${BACKUP_AUTH:-}" ] && [ ! -f "${AUTH_CONF}" ]; then
      cp -a "${BACKUP_AUTH}" "${AUTH_CONF}" && info "  auth.conf restored"
    fi
    if [ -d "${BACKUP_SEEDS:-}" ] && [ ! -d "${SEEDS_DIR}" ]; then
      cp -a "${BACKUP_SEEDS}" "${SEEDS_DIR}" && info "  seeds restored"
    fi
    echo -e "${RED}[x]${NC} Regeneration aborted — state restored from ${epoch}" >&2
    exit 1
  }
  trap restore_backups ERR INT TERM

  # Only delete after both backups succeeded
  rm -f "${AUTH_CONF}"
  rm -rf "${SEEDS_DIR}"
  info "Old keys wiped. Regenerating..."
fi

# ── idempotency check ──────────────────────────────────────────────────────────
if [ -f "${AUTH_CONF}" ]; then
  # NB12: loud warn + explicit identity-count check so an operator upgrading
  # from an older auth.conf (e.g. pre-#706 with only 5 identities) is told
  # that their conf is out of date and they must --regenerate.
  existing_count=$(grep -cE '^[[:space:]]*# [a-z][a-z0-9-]*$' "${AUTH_CONF}" 2>/dev/null || echo 0)
  expected_count=${#IDENTITIES[@]}
  if [ "${existing_count}" -lt "${expected_count}" ]; then
    warn "auth.conf has ${existing_count} identities; spec #706/#689 requires ${expected_count}."
    warn "Run: sudo ./deploy/nats/gen-nkeys.sh --regenerate --yes"
    warn "(backs up auth.conf + seeds to .bak.\$(date +%s) before rotating)"
  else
    warn "auth.conf already exists with ${existing_count} identities — skipping."
  fi
  warn "To re-apply permissions only: sudo ./deploy/nats/gen-nkeys.sh --fix-perms"
  warn "To atomically back up and regenerate: sudo ./deploy/nats/gen-nkeys.sh --regenerate"
  exit 0
fi

# ── locate or download nk ─────────────────────────────────────────────────────

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
      || error "SHA-256 check failed for ${url} — aborting before installing nk binary"
  else
    warn "SHA256SUMS not available for this release — skipping integrity check"
  fi

  cp "${nk_bin}" /usr/local/bin/nk
  info "nk v${version} installed to /usr/local/bin/nk"
  echo "/usr/local/bin/nk"
}

NK_BIN=$(ensure_nk)

# ── create directories ────────────────────────────────────────────────────────
apply_permissions   # creates SEEDS_DIR with correct ownership before writing seeds
mkdir -p "${AUTH_DIR}"
chown root:nats "${AUTH_DIR}"
chmod 750 "${AUTH_DIR}"

# ── generate nkey pairs (T1.5: extended to 7; generate_nkey defined earlier) ──

info "Generating nkey pairs in ${SEEDS_DIR}/ ..."
HUB_PUB=$(generate_nkey "hub")
TELEGRAM_PUB=$(generate_nkey "telegram-adapter")
DISCORD_PUB=$(generate_nkey "discord-adapter")
TTS_PUB=$(generate_nkey "tts-adapter")
STT_PUB=$(generate_nkey "stt-adapter")
VOICE_TTS_PUB=$(generate_nkey "voice-tts")
VOICE_STT_PUB=$(generate_nkey "voice-stt")
WORKER_PUB=$(generate_nkey "llm-worker")
IMAGE_WORKER_PUB=$(generate_nkey "image-worker")
MONITOR_PUB=$(generate_nkey "monitor")

# ── write auth.conf via render_auth_conf (T1.6) ───────────────────────────────
declare -A PUBKEYS=(
  [hub]="${HUB_PUB}"
  [telegram-adapter]="${TELEGRAM_PUB}"
  [discord-adapter]="${DISCORD_PUB}"
  [tts-adapter]="${TTS_PUB}"
  [stt-adapter]="${STT_PUB}"
  [voice-tts]="${VOICE_TTS_PUB}"
  [voice-stt]="${VOICE_STT_PUB}"
  [llm-worker]="${WORKER_PUB}"
  [image-worker]="${IMAGE_WORKER_PUB}"
  [monitor]="${MONITOR_PUB}"
)
render_auth_conf PUBKEYS > "${AUTH_CONF}"

chown root:nats "${AUTH_CONF}"
chmod 0640 "${AUTH_CONF}"

# B4: auth.conf written successfully — clear the restore trap.
trap - ERR INT TERM

info "Done."
info "  Seeds:     ${SEEDS_DIR}/"
info "  auth.conf: ${AUTH_CONF}"
info ""
info "  hub.seed               NATS_NKEY_SEED_PATH=${SEEDS_DIR}/hub.seed"
info "  telegram-adapter.seed  NATS_NKEY_SEED_PATH=${SEEDS_DIR}/telegram-adapter.seed"
info "  discord-adapter.seed   NATS_NKEY_SEED_PATH=${SEEDS_DIR}/discord-adapter.seed"
info "  tts-adapter.seed       NATS_NKEY_SEED_PATH=${SEEDS_DIR}/tts-adapter.seed"
info "  stt-adapter.seed       NATS_NKEY_SEED_PATH=${SEEDS_DIR}/stt-adapter.seed"
info "  voice-tts.seed         NATS_NKEY_SEED_PATH=${SEEDS_DIR}/voice-tts.seed   (voicecli nats-serve tts)"
info "  voice-stt.seed         NATS_NKEY_SEED_PATH=${SEEDS_DIR}/voice-stt.seed   (voicecli nats-serve stt)"
info "  llm-worker.seed        NATS_NKEY_SEED_PATH=${SEEDS_DIR}/llm-worker.seed"
info "  image-worker.seed      NATS_NKEY_SEED_PATH=${SEEDS_DIR}/image-worker.seed  (imagecli nats-serve)"
info "  monitor.seed           NATS_NKEY_SEED_PATH=${SEEDS_DIR}/monitor.seed"
warn "Supervisor confs already reference ~/.lyra/nkeys/ — no changes needed."
