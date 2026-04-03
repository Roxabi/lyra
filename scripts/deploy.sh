#!/bin/bash
# Deploy script for production — pull latest code, install deps, restart changed services.
# Called by: systemd timer (auto) or `make deploy` (manual from dev machine).
#
# Checks two repos:
# - lyra (main project) — always checked, restarts adapters on new commits
# - voiceCLI (voice services) — checked independently, restarts TTS/STT on new commits
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

LYRA_DIR="$HOME/projects/lyra"
VOICE_DIR="$HOME/projects/voiceCLI"
SCTL="$HOME/projects/lyra-stack/scripts/supervisorctl.sh"
LOG_FILE="$HOME/.local/state/lyra/logs/deploy.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

LYRA_UPDATED=false
VOICE_UPDATED=false

# ── Check lyra ───────────────────────────────────────────────────────────────

cd "$LYRA_DIR"
git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

LYRA_LOCAL=$(git rev-parse HEAD)
LYRA_REMOTE=$(git rev-parse origin/staging)

if [ "$LYRA_LOCAL" != "$LYRA_REMOTE" ]; then
    log "lyra: new version $LYRA_LOCAL -> $LYRA_REMOTE"

    # Reset generated files that may differ between machines (e.g. uv.lock after voiceCLI re-lock)
    git checkout -- uv.lock 2>/dev/null || true
    git pull origin staging 2>&1 | tee -a "$LOG_FILE"
    uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

    if ! uv run pytest --tb=short -q 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR: lyra tests failed — rolling back."
        git reset --hard "$LYRA_LOCAL" 2>&1 | tee -a "$LOG_FILE"
    else
        LYRA_UPDATED=true
    fi
fi

# ── Check voiceCLI ───────────────────────────────────────────────────────────

if [ -d "$VOICE_DIR/.git" ]; then
    cd "$VOICE_DIR"
    git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

    VOICE_LOCAL=$(git rev-parse HEAD)
    VOICE_REMOTE=$(git rev-parse origin/staging)

    if [ "$VOICE_LOCAL" != "$VOICE_REMOTE" ]; then
        log "voiceCLI: new version $VOICE_LOCAL -> $VOICE_REMOTE"

        git pull origin staging 2>&1 | tee -a "$LOG_FILE"
        uv sync --frozen 2>&1 | tee -a "$LOG_FILE"
        VOICE_UPDATED=true

        # Also update voiceCLI inside Lyra's .venv so the library stays in sync
        log "Re-locking voiceCLI in Lyra..."
        cd "$LYRA_DIR"
        uv lock --upgrade-package voicecli 2>&1 | tee -a "$LOG_FILE"
        uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"
        LYRA_UPDATED=true
    fi
fi

# ── Restart changed services ─────────────────────────────────────────────────

if [ "$LYRA_UPDATED" = false ] && [ "$VOICE_UPDATED" = false ]; then
    log "All repos up to date."
    exit 0
fi

if [ -f "$HOME/projects/lyra-stack/supervisord.pid" ] && kill -0 "$(cat "$HOME/projects/lyra-stack/supervisord.pid")" 2>/dev/null; then
    if [ "$LYRA_UPDATED" = true ]; then
        log "Restarting Lyra adapters..."
        "$SCTL" restart lyra_telegram lyra_discord 2>&1 | tee -a "$LOG_FILE"
    fi

    if [ "$VOICE_UPDATED" = true ]; then
        log "Restarting voice services..."
        "$SCTL" restart voicecli_tts voicecli_stt 2>&1 | tee -a "$LOG_FILE"
    fi
else
    log "Starting supervisor (not running)..."
    "$HOME/projects/lyra-stack/scripts/start.sh" 2>&1 | tee -a "$LOG_FILE"
fi

TAGS=""
[ "$LYRA_UPDATED" = true ] && TAGS="${TAGS} lyra=$(cd "$LYRA_DIR" && git rev-parse --short HEAD)"
[ "$VOICE_UPDATED" = true ] && TAGS="${TAGS} voice=$(cd "$VOICE_DIR" && git rev-parse --short HEAD)"
log "Deploy complete:${TAGS}"
