#!/bin/bash
# Deploy script for production — pull latest staging, install deps, restart changed services.
# Called by: systemd timer (auto) or `make deploy` (manual from dev machine).
#
# Smart restart: only restarts services whose dependencies actually changed.
# - lyra_telegram + lyra_discord: always restarted on new commits
# - voicecli_tts + voicecli_stt: only restarted if voiceCLI dependency changed in uv.lock
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

PROJECT_DIR="$HOME/projects/lyra"
SCTL="$HOME/projects/lyra-stack/scripts/supervisorctl.sh"
LOG_FILE="$HOME/.local/state/lyra/logs/deploy.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

cd "$PROJECT_DIR"

# Fetch latest
git fetch origin staging 2>&1 | tee -a "$LOG_FILE"

# Check if there are new commits
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/staging)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL)"
    exit 0
fi

log "New version detected: $LOCAL -> $REMOTE"

# Snapshot uv.lock before pull to detect dependency changes
LOCK_BEFORE=""
if [ -f uv.lock ]; then
    LOCK_BEFORE=$(sha256sum uv.lock | cut -d' ' -f1)
fi

# Pull
git pull origin staging 2>&1 | tee -a "$LOG_FILE"

# Install/update deps
uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

# Run tests
if ! uv run pytest --tb=short -q 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: Tests failed — NOT restarting. Rolling back."
    git checkout "$LOCAL" 2>&1 | tee -a "$LOG_FILE"
    exit 1
fi

# Detect if voiceCLI dependency changed
LOCK_AFTER=$(sha256sum uv.lock | cut -d' ' -f1)
VOICE_CHANGED=false
if [ "$LOCK_BEFORE" != "$LOCK_AFTER" ]; then
    if git diff "$LOCAL" "$REMOTE" -- uv.lock | grep -q 'voicecli'; then
        VOICE_CHANGED=true
    fi
fi

# Restart services via supervisor
if [ -f "$HOME/projects/lyra-stack/supervisord.pid" ] && kill -0 "$(cat "$HOME/projects/lyra-stack/supervisord.pid")" 2>/dev/null; then
    log "Restarting Lyra adapters..."
    "$SCTL" restart lyra_telegram lyra_discord 2>&1 | tee -a "$LOG_FILE"

    if [ "$VOICE_CHANGED" = true ]; then
        log "voiceCLI dependency changed — restarting TTS/STT..."
        "$SCTL" restart voicecli_tts voicecli_stt 2>&1 | tee -a "$LOG_FILE"
    fi
else
    log "Starting supervisor (not running)..."
    "$HOME/projects/lyra-stack/scripts/start.sh" 2>&1 | tee -a "$LOG_FILE"
fi

log "Deploy complete: $(git rev-parse --short HEAD)$([ "$VOICE_CHANGED" = true ] && echo ' [+voice]')"
