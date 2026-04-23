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
SCTL="$HOME/projects/lyra/deploy/supervisor/supervisorctl.sh"
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
        timeout 60 uv sync --frozen 2>&1 | tee -a "$LOG_FILE"
        VOICE_UPDATED=true
    fi
fi

# ── Check roxabi-* repos (pull only — no tests, no service restart) ──────────

for repo in "$HOME/projects"/roxabi-*; do
    [ -d "$repo/.git" ] || continue
    cd "$repo"
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    timeout 30 git fetch origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE" || continue
    RX_LOCAL=$(git rev-parse HEAD)
    RX_REMOTE=$(git rev-parse "origin/$BRANCH")
    if [ "$RX_LOCAL" != "$RX_REMOTE" ]; then
        log "$(basename "$repo"): new version $RX_LOCAL -> $RX_REMOTE ($BRANCH)"
        timeout 30 git pull --ff-only origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"
    fi
done

# ── Restart changed services ─────────────────────────────────────────────────

if [ "$LYRA_UPDATED" = false ] && [ "$VOICE_UPDATED" = false ]; then
    exit 0
fi

if [ -f "$HOME/projects/lyra/deploy/supervisor/supervisord.pid" ] && kill -0 "$(cat "$HOME/projects/lyra/deploy/supervisor/supervisord.pid")" 2>/dev/null; then
    if [ "$LYRA_UPDATED" = true ]; then
        log "Restarting Lyra (hub first, then adapters)..."
        "$SCTL" restart lyra-hub 2>&1 | tee -a "$LOG_FILE"
        # Readiness loop: gates adapter startup — adapters must not start until hub is stable.
        log "Waiting for lyra-hub to reach RUNNING..."
        HUB_READY=false
        i=1
        while [ "$i" -le 12 ]; do
            sleep 3
            HUB_STATE=$("$SCTL" status lyra-hub 2>&1 | grep -oE 'RUNNING|STARTING|FATAL|STOPPED|EXITED|BACKOFF' | head -1) || true
            if [ "$HUB_STATE" = "RUNNING" ]; then
                # Stabilization hold: re-check after 2s to filter STARTING→RUNNING→BACKOFF flaps
                sleep 2
                HUB_STATE=$("$SCTL" status lyra-hub 2>&1 | grep -oE 'RUNNING|STARTING|FATAL|STOPPED|EXITED|BACKOFF' | head -1) || true
                if [ "$HUB_STATE" = "RUNNING" ]; then
                    log "lyra-hub is RUNNING — starting adapters"
                    HUB_READY=true
                    break
                fi
                log "lyra-hub flapped (attempt $i/12)"
                i=$(( i + 1 ))
                continue
            fi
            log "lyra-hub state: ${HUB_STATE:-unknown} (attempt $i/12)"
            i=$(( i + 1 ))
        done
        if [ "$HUB_READY" = false ]; then
            log "ERROR: lyra-hub did not reach RUNNING after 60s — stopping adapters and aborting"
            "$SCTL" stop lyra-telegram lyra-discord 2>&1 | tee -a "$LOG_FILE"
            exit 1
        fi
        "$SCTL" restart lyra-telegram lyra-discord 2>&1 | tee -a "$LOG_FILE"
    fi

    if [ "$VOICE_UPDATED" = true ]; then
        log "Restarting voice services..."
        "$SCTL" restart voicecli_tts voicecli_stt 2>&1 | tee -a "$LOG_FILE"
    fi
else
    log "Starting supervisor (not running)..."
    "$HOME/projects/lyra/deploy/supervisor/start.sh" 2>&1 | tee -a "$LOG_FILE"
fi

# ── Verify services reached RUNNING ──────────────────────────────────────────
# Post-restart verify loop: checks final steady state of ALL services (hub + adapters + voice),
# independent of the hub readiness loop above which only gates adapter startup.

log "Verifying services..."
HEALTHY=false
i=1
while [ "$i" -le 12 ]; do
    sleep 5
    FAILED=$("$SCTL" status 2>&1 | grep -c "FATAL\|BACKOFF" || true)
    if [ "$FAILED" -eq 0 ]; then
        HEALTHY=true
        break
    fi
    log "Waiting for services... (attempt $i/12)"
    i=$(( i + 1 ))
done

if [ "$HEALTHY" = false ]; then
    log "ERROR: Some services failed to reach RUNNING after 60s:"
    "$SCTL" status 2>&1 | tee -a "$LOG_FILE"
    exit 1
fi

TAGS=""
[ "$LYRA_UPDATED" = true ] && TAGS="${TAGS} lyra=$(cd "$LYRA_DIR" && git rev-parse --short HEAD)"
[ "$VOICE_UPDATED" = true ] && TAGS="${TAGS} voice=$(cd "$VOICE_DIR" && git rev-parse --short HEAD)"
log "Deploy complete:${TAGS}"
