#!/bin/bash
# Deploy script for Machine 1 — pull latest main, install deps, restart Lyra.
# Called by: systemd timer (auto) or `make deploy` (manual from Machine 2).
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

PROJECT_DIR="$HOME/projects/lyra"
SUPERVISOR_DIR="$PROJECT_DIR/supervisor"
SUPERVISORCTL="$SUPERVISOR_DIR/scripts/supervisorctl.sh"
LOG_FILE="$HOME/.lyra/logs/deploy.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

cd "$PROJECT_DIR"

# Fetch latest
git fetch origin main 2>&1 | tee -a "$LOG_FILE"

# Check if there are new commits
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    log "Already up to date ($LOCAL)"
    exit 0
fi

log "New version detected: $LOCAL -> $REMOTE"

# Pull
git pull origin main 2>&1 | tee -a "$LOG_FILE"

# Install/update deps
uv sync --all-extras --frozen 2>&1 | tee -a "$LOG_FILE"

# Run tests
if ! uv run pytest --tb=short -q 2>&1 | tee -a "$LOG_FILE"; then
    log "ERROR: Tests failed — NOT restarting. Rolling back."
    git checkout "$LOCAL" 2>&1 | tee -a "$LOG_FILE"
    exit 1
fi

# Restart Lyra via supervisor
if [ -f "$SUPERVISOR_DIR/supervisord.pid" ] && kill -0 "$(cat "$SUPERVISOR_DIR/supervisord.pid")" 2>/dev/null; then
    log "Restarting Lyra..."
    "$SUPERVISORCTL" restart lyra 2>&1 | tee -a "$LOG_FILE"
else
    log "Starting Lyra (supervisor not running)..."
    "$SUPERVISOR_DIR/scripts/start.sh" 2>&1 | tee -a "$LOG_FILE"
fi

log "Deploy complete: $(git rev-parse --short HEAD)"
