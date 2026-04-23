#!/bin/bash
# Start lyra supervisord.
# Usage: start.sh          — start supervisord only (programs stay stopped)
#        start.sh --all    — start supervisord + all programs (used by lyra.service)
set -e

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$SCRIPT_DIR"

mkdir -p "$HOME/.local/state/lyra/logs"
mkdir -p "$HOME/.local/state/voicecli/logs"

if [ -f "$SUPERVISOR_DIR/supervisord.pid" ]; then
    PID=$(cat "$SUPERVISOR_DIR/supervisord.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "✓ supervisord already running (PID: $PID)"
        "$SCRIPT_DIR/supervisorctl.sh" status || true
        exit 0
    else
        echo "Stale PID file, removing..."
        rm -f "$SUPERVISOR_DIR/supervisord.pid" "$SUPERVISOR_DIR/supervisor.sock"
    fi
fi

echo "Starting supervisord..."
"$HOME/.local/bin/supervisord" -c "$SUPERVISOR_DIR/supervisord.conf"
sleep 2
echo "✓ supervisord started"

if [ "${1:-}" = "--all" ]; then
    # Wait for NATS if .env has a NATS_URL (production three-process mode)
    ENV_FILE="$(dirname "$SUPERVISOR_DIR")/.env"
    if [ -f "$ENV_FILE" ] && grep -q "^NATS_URL=" "$ENV_FILE"; then
        echo "Waiting for NATS on 127.0.0.1:4222..."
        for _ in $(seq 30); do
            nc -z 127.0.0.1 4222 2>/dev/null && break
            sleep 1
        done
        if nc -z 127.0.0.1 4222 2>/dev/null; then
            echo "✓ NATS is ready"
        else
            echo "⚠ NATS not available after 30s — starting programs anyway"
        fi
    fi
    echo "Starting all programs..."
    "$SCRIPT_DIR/supervisorctl.sh" start all
fi

echo ""
"$SCRIPT_DIR/supervisorctl.sh" status || true
