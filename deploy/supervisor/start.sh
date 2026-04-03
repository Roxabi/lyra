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
    echo "Starting all programs..."
    "$SCRIPT_DIR/supervisorctl.sh" start all
fi

echo ""
"$SCRIPT_DIR/supervisorctl.sh" status || true
