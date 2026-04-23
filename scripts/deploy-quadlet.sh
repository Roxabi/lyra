#!/bin/bash
# Deploy script for production — Quadlet/podman path.
# Pulls latest code, rebuilds the lyra image, restarts systemd user services.
# Called by: systemd timer (auto) or manual invocation on M₁.
#
# Checks two repos:
# - lyra (main project) — always checked; on new commits: test → build → restart containers
# - voiceCLI (voice services) — checked independently; baked into the image, triggers rebuild
#
# Does NOT touch nats.service — managed independently (nkey regen flow, DEPLOYMENT.md §10).
set -euo pipefail
umask 0077
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

LYRA_DIR="$HOME/projects/lyra"
VOICE_DIR="$HOME/projects/voiceCLI"
LOG_FILE="$HOME/.local/state/lyra/logs/deploy.log"
FAIL_FILE="$HOME/.local/state/lyra/deploy_failed_shas.txt"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

LYRA_UPDATED=false
VOICE_UPDATED=false

# ── Check lyra ───────────────────────────────────────────────────────────────

cd "$LYRA_DIR"
timeout 30 git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

LYRA_LOCAL=$(git rev-parse HEAD)
LYRA_REMOTE=$(git rev-parse origin/staging)

if [ "$LYRA_LOCAL" != "$LYRA_REMOTE" ]; then
    # Skip SHAs we've already rolled back — prevents pull/test/fail/rollback loops on broken staging.
    # Delete $FAIL_FILE to force a retry on a known-failing SHA.
    if [ -f "$FAIL_FILE" ] && grep -Fxq "$LYRA_REMOTE" "$FAIL_FILE"; then
        : # known-failing SHA — wait silently for a new commit
    else
        log "lyra: new version $LYRA_LOCAL -> $LYRA_REMOTE"

        # Reset generated files that may differ between machines (e.g. uv.lock after voiceCLI re-lock)
        git checkout -- uv.lock 2>/dev/null || true
        timeout 30 git pull origin staging 2>&1 | tee -a "$LOG_FILE"
        timeout 60 uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

        if ! timeout 120 uv run pytest --tb=short -q 2>&1 | tee -a "$LOG_FILE"; then
            log "ERROR: lyra tests failed for $LYRA_REMOTE — rolling back and marking SHA as bad."
            echo "$LYRA_REMOTE" >> "$FAIL_FILE"
            git reset --hard "$LYRA_LOCAL" 2>&1 | tee -a "$LOG_FILE"
            uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"
            exit 1
        else
            LYRA_UPDATED=true
            # Clear fail log on success so it doesn't grow unbounded.
            rm -f "$FAIL_FILE"
        fi
    fi
fi

# ── Check voiceCLI ───────────────────────────────────────────────────────────

if [ -d "$VOICE_DIR/.git" ]; then
    cd "$VOICE_DIR"
    timeout 30 git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

    VOICE_LOCAL=$(git rev-parse HEAD)
    VOICE_REMOTE=$(git rev-parse origin/staging)

    if [ "$VOICE_LOCAL" != "$VOICE_REMOTE" ]; then
        log "voiceCLI: new version $VOICE_LOCAL -> $VOICE_REMOTE"

        timeout 30 git pull origin staging 2>&1 | tee -a "$LOG_FILE"
        # Upgrade voicecli in the lyra venv — baked into image at build time.
        cd "$LYRA_DIR"
        timeout 60 uv sync --all-extras --upgrade-package voicecli 2>&1 | tee -a "$LOG_FILE"
        VOICE_UPDATED=true
    fi
fi

# ── Early exit if nothing changed ────────────────────────────────────────────

if [ "$LYRA_UPDATED" = false ] && [ "$VOICE_UPDATED" = false ]; then
    exit 0
fi

# ── Rebuild image ─────────────────────────────────────────────────────────────
# voicecli is baked into the image, so any change to either repo requires a rebuild.

cd "$LYRA_DIR"
log "Tagging current image as rollback..."
podman tag localhost/lyra:latest localhost/lyra:rollback 2>/dev/null || log "No existing image to tag (first deploy)"
log "Building localhost/lyra:latest..."
timeout 300 podman build -f Dockerfile -t localhost/lyra:latest . 2>&1 | tee -a "$LOG_FILE"
log "Image build complete."
mkdir -p "$HOME/.lyra"
EXPECTED_IMAGE_ID=$(podman inspect --format '{{.Id}}' localhost/lyra:latest)
log "Built image ID: $EXPECTED_IMAGE_ID"
echo "$EXPECTED_IMAGE_ID" > "$HOME/.lyra/.image-digest"

# ── Restart containers ────────────────────────────────────────────────────────
# Restart hub first, wait for it to be active, then restart adapters.
# nats.service is intentionally excluded — managed independently.

CURRENT_IMAGE_ID=$(podman inspect --format '{{.Id}}' localhost/lyra:latest)
if [ "$CURRENT_IMAGE_ID" != "$EXPECTED_IMAGE_ID" ]; then
    log "ERROR: localhost/lyra:latest image ID changed between build and restart."
    log "  Expected: $EXPECTED_IMAGE_ID"
    log "  Current:  $CURRENT_IMAGE_ID"
    log "  Possible supply-chain tampering. Aborting deploy."
    exit 1
fi
log "Image ID verified — proceeding with restart."

log "Verifying env files in ~/.lyra/env/..."
for role in hub telegram discord; do
  env_file="$HOME/.lyra/env/$role.env"
  if [[ ! -f "$env_file" ]]; then
    log "ERROR: missing $env_file"
    log "  Copy deploy/quadlet/$role.env.example → $env_file and fill in secrets"
    exit 1
  fi
  mode=$(stat -c %a "$env_file")
  if [[ "$mode" != "600" ]]; then
    log "ERROR: $env_file has mode $mode, expected 600"
    log "  Run: chmod 600 $env_file"
    exit 1
  fi
done
log "Env files OK."

log "Removing old containers to force new image pickup..."
podman rm -f lyra-hub lyra-telegram lyra-discord 2>/dev/null || true
log "Restarting lyra-hub.service..."
systemctl --user restart lyra-hub.service 2>&1 | tee -a "$LOG_FILE"

# Hub readiness loop: gate adapter restart until hub is active and stable.
log "Waiting for lyra-hub.service to reach active (running)..."
HUB_READY=false
i=1
while [ "$i" -le 12 ]; do
    sleep 5
    HUB_STATE=$(systemctl --user is-active lyra-hub.service 2>/dev/null || true)
    if [ "$HUB_STATE" = "active" ]; then
        # Stabilization hold: re-check after 2s to filter activating→active→failed flaps.
        sleep 2
        HUB_STATE=$(systemctl --user is-active lyra-hub.service 2>/dev/null || true)
        if [ "$HUB_STATE" = "active" ]; then
            log "lyra-hub.service is active — restarting adapters"
            HUB_READY=true
            break
        fi
        log "lyra-hub.service flapped (attempt $i/12)"
        i=$(( i + 1 ))
        continue
    fi
    log "lyra-hub.service state: ${HUB_STATE:-unknown} (attempt $i/12)"
    i=$(( i + 1 ))
done

if [ "$HUB_READY" = false ]; then
    log "ERROR: lyra-hub.service did not reach active after 60s — stopping adapters and aborting"
    systemctl --user stop lyra-telegram.service lyra-discord.service 2>&1 | tee -a "$LOG_FILE"
    exit 1
fi

log "Restarting lyra-telegram.service lyra-discord.service..."
systemctl --user restart lyra-telegram.service lyra-discord.service 2>&1 | tee -a "$LOG_FILE"

# ── Verify all three services reached active ──────────────────────────────────

log "Verifying services..."
HEALTHY=false
i=1
while [ "$i" -le 12 ]; do
    sleep 5
    FAILED=0
    for svc in lyra-hub.service lyra-telegram.service lyra-discord.service; do
        STATE=$(systemctl --user is-active "$svc" 2>/dev/null || true)
        if [ "$STATE" != "active" ]; then
            FAILED=$(( FAILED + 1 ))
        fi
    done
    if [ "$FAILED" -eq 0 ]; then
        HEALTHY=true
        break
    fi
    log "Waiting for services... ($FAILED not active, attempt $i/12)"
    i=$(( i + 1 ))
done

if [ "$HEALTHY" = false ]; then
    log "ERROR: Some services failed to reach active after 60s:"
    systemctl --user status lyra-hub.service lyra-telegram.service lyra-discord.service --no-pager --lines=0 2>&1 | tee -a "$LOG_FILE"
    exit 1
fi

TAGS=""
[ "$LYRA_UPDATED" = true ] && TAGS="${TAGS} lyra=$(cd "$LYRA_DIR" && git rev-parse --short HEAD)"
[ "$VOICE_UPDATED" = true ] && TAGS="${TAGS} voice=$(cd "$VOICE_DIR" && git rev-parse --short HEAD)"
log "Deploy complete:${TAGS}"
