#!/usr/bin/env bash
# deploy/install.sh — idempotent Quadlet install for lyra
#
# Absorbs: make quadlet-install + make quadlet-secrets-install
# Does NOT restart services — defer to operator.
#
# Usage:
#   ./deploy/install.sh              # install units + secrets (skip if already present)
#   ./deploy/install.sh --dry-run    # print actions without executing
#   ./deploy/install.sh --secrets-only  # only (re)create Podman secrets
#   ./deploy/install.sh --force      # force --replace on secrets even if present
#
# Hard constraints (S5, grandfathered):
#   nkey seeds: ~/.lyra/nkeys/
#   env files:  ~/.lyra/env/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUADLET_SRC="${SCRIPT_DIR}/quadlet"
QUADLET_DST="${HOME}/.config/containers/systemd"
NKEYS_DIR="${HOME}/.lyra/nkeys"

DRY_RUN=0
SECRETS_ONLY=0
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --dry-run)       DRY_RUN=1 ;;
    --secrets-only)  SECRETS_ONLY=1 ;;
    --force)         FORCE=1 ;;
    *)               echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }

# ── 1. Verify nkeys dir ──────────────────────────────────────────────────────

log "Checking ~/.lyra/nkeys/ ..."
if [[ ! -d "${NKEYS_DIR}" ]]; then
  echo "ERROR: ${NKEYS_DIR} not found. Run: make nats-setup" >&2
  exit 1
fi

# Refuse to install with placeholder nkeys still in the live auth.conf.
# Skipped under --dry-run so the operator can still preview install actions.
if [[ "$DRY_RUN" -eq 0 ]] \
  && [[ -f "${NKEYS_DIR}/auth.conf" ]] \
  && grep -qE '^[[:space:]]*nkey:[[:space:]]+"UDET' "${NKEYS_DIR}/auth.conf"; then
  echo "ERROR: ${NKEYS_DIR}/auth.conf still contains UDET* placeholder pubkeys." >&2
  echo "       Run: make nats-regen-authconf  (renders nkeys from ${NKEYS_DIR}/*.seed)" >&2
  exit 1
fi

declare -A SEEDS=(
  [lyra-nats-auth]="${NKEYS_DIR}/auth.conf"
  [lyra-nats-hub]="${NKEYS_DIR}/hub.seed"
  [lyra-nats-telegram]="${NKEYS_DIR}/telegram-adapter.seed"
  [lyra-nats-discord]="${NKEYS_DIR}/discord-adapter.seed"
  [lyra-nats-clipool]="${NKEYS_DIR}/clipool-worker.seed"
  [lyra-nats-turn-writer]="${NKEYS_DIR}/turn-writer.seed"
  [lyra_blobstore_token]="${HOME}/.lyra/blobstore.tok"
)

# ── 1b. Generate blobstore bearer token (idempotent) ────────────────────────

BLOBSTORE_TOK="${HOME}/.lyra/blobstore.tok"
if [[ ! -f "${BLOBSTORE_TOK}" || "$FORCE" -eq 1 ]]; then
  log "Generating blobstore bearer token → ${BLOBSTORE_TOK} ..."
  run mkdir -p "${HOME}/.lyra"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    (umask 0077; openssl rand -base64 48 > "${BLOBSTORE_TOK}")
  else
    echo "[dry-run] would generate ${BLOBSTORE_TOK} (48 url-safe chars, mode 0600)"
  fi
else
  echo "  [skip] ${BLOBSTORE_TOK} already exists (use --force to regenerate)"
fi

# ── 1c. Bootstrap blobstore.env (idempotent) ─────────────────────────────────

ENV_FILE="${HOME}/.lyra/env/blobstore.env"
if [[ ! -f "${ENV_FILE}" || "$FORCE" -eq 1 ]]; then
  log "Generating ${ENV_FILE} ..."
  run mkdir -p "$(dirname "${ENV_FILE}")"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
    # `run` only wraps exec; stream redirection (>) is dry-run-gated via the if block above.
    (umask 0077; printf 'TAILSCALE_IPV4=%s\n' "${TS_IP}" > "${ENV_FILE}")
    log "[ok] generated ${ENV_FILE} (TAILSCALE_IPV4=${TS_IP:-<empty>})"
  else
    echo "[dry-run] would generate ${ENV_FILE} (TAILSCALE_IPV4 from tailscale ip -4)"
  fi
else
  echo "  [skip] ${ENV_FILE} already exists (use --force to regenerate)"
fi

MISSING=0
for secret_name in "${!SEEDS[@]}"; do
  seed_path="${SEEDS[$secret_name]}"
  if [[ ! -f "${seed_path}" ]]; then
    warn "Missing seed file: ${seed_path} (needed for secret ${secret_name})"
    MISSING=1
  fi
done
if [[ "$MISSING" -eq 1 ]]; then
  echo "ERROR: Missing seed files — cannot install secrets. Run: make nats-setup" >&2
  exit 1
fi

# ── 2. Install Podman secrets ────────────────────────────────────────────────

log "Installing Podman secrets ..."
for secret_name in "${!SEEDS[@]}"; do
  seed_path="${SEEDS[$secret_name]}"
  already_exists=$(podman secret ls --format '{{.Name}}' 2>/dev/null | grep -Fx "${secret_name}" || true)
  if [[ -n "${already_exists}" && "$FORCE" -eq 0 ]]; then
    echo "  [skip] ${secret_name} already exists (use --force to replace)"
  else
    run podman secret create --replace "${secret_name}" "${seed_path}"
    echo "  [ok]   ${secret_name}"
  fi
done

# Optional secrets — skip if source files absent (not fatal)
if [[ -f "${HOME}/.lyra/gh-app.pem" ]]; then
  already_exists=$(podman secret ls --format '{{.Name}}' 2>/dev/null | grep -Fx "lyra-gh-pem" || true)
  if [[ -n "${already_exists}" && "$FORCE" -eq 0 ]]; then
    echo "  [skip] lyra-gh-pem already exists"
  else
    run podman secret create --replace lyra-gh-pem "${HOME}/.lyra/gh-app.pem"
    echo "  [ok]   lyra-gh-pem"
  fi
else
  warn "~/.lyra/gh-app.pem not found — lyra-gh-pem secret not created"
fi

if [[ -f "${HOME}/.lyra/claude-oauth.tok" ]]; then
  already_exists=$(podman secret ls --format '{{.Name}}' 2>/dev/null | grep -Fx "lyra-claude-oauth" || true)
  if [[ -n "${already_exists}" && "$FORCE" -eq 0 ]]; then
    echo "  [skip] lyra-claude-oauth already exists"
  else
    run bash -c "tr -d '\\n' < '${HOME}/.lyra/claude-oauth.tok' | podman secret create --replace lyra-claude-oauth -"
    echo "  [ok]   lyra-claude-oauth"
  fi
else
  warn "~/.lyra/claude-oauth.tok not found — lyra-claude-oauth secret not created"
fi

if [[ "$SECRETS_ONLY" -eq 1 ]]; then
  log "Done (--secrets-only)."
  exit 0
fi

# ── 3. Ensure data directories ──────────────────────────────────────────────

log "Ensuring data directories ..."
run mkdir -p /data/lyra/blobs
if ! findmnt /data/lyra/blobs >/dev/null 2>&1; then
  warn "/data/lyra/blobs is not a mount point — add to /etc/fstab with noatime,nodiratime"
fi
echo "  [ok]   /data/lyra/blobs"
run ln -sf /data/lyra/blobs ~/.lyra/blobstore
echo "  [ok]   ~/.lyra/blobstore → /data/lyra/blobs"
run mkdir -p "${HOME}/.lyra/turn-writer"
echo "  [ok]   ~/.lyra/turn-writer/"

# ── 4. Copy Quadlet units ────────────────────────────────────────────────────

log "Copying Quadlet units to ${QUADLET_DST} ..."
run mkdir -p "${QUADLET_DST}"
for f in "${QUADLET_SRC}"/*.container "${QUADLET_SRC}"/*.network "${QUADLET_SRC}"/*.volume "${QUADLET_SRC}"/*.pod; do
  [[ -e "$f" ]] || continue
  run cp "$f" "${QUADLET_DST}/"
  echo "  [cp]   $(basename "$f")"
done

# ── 5. daemon-reload ─────────────────────────────────────────────────────────

log "Reloading systemd user daemon ..."
run systemctl --user daemon-reload
echo "  [ok]   daemon-reload"

log "Enabling lyra-blobstore.service ..."
run systemctl --user enable lyra-blobstore.service
echo "  [ok]   lyra-blobstore.service enabled"

# ── 5b. Seed BotStore from config.toml (idempotent) ─────────────────────────
# Required since #1416: Authenticator reads from BotStore, not config.toml.
# Skipping this causes a hub crash-loop on first boot.
log "Seeding BotStore from config.toml ..."
run podman run --rm \
    -v "${HOME}/.lyra:/home/lyra/.lyra:z" \
    -v "${HOME}/.lyra/config.toml:/app/config.toml:ro,z" \
    ghcr.io/roxabi/lyra:staging-svc \
    lyra bot init

echo "  [ok]   BotStore seeded"

# ── 6. Install sync timer + service (idempotent) ───────────────────────────

log "Installing lyra-quadlet-sync timer + service ..."
run make quadlet-sync-install

log "Done. Services NOT restarted — run: systemctl --user start lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool lyra-gh-helper lyra-turn-writer lyra-blobstore"
