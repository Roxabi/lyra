#!/bin/bash
# Stop supervisord and all managed processes
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$SUPERVISOR_DIR")"

source "$PROJECT_DIR/.venv/bin/activate"

if [ -f "$SUPERVISOR_DIR/supervisord.pid" ]; then
    PID=$(cat "$SUPERVISOR_DIR/supervisord.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping all processes..."
        "$SCRIPT_DIR/supervisorctl.sh" stop all 2>/dev/null || true
        echo "Stopping supervisord..."
        kill "$PID" 2>/dev/null || true
        sleep 1
        echo "✓ stopped"
    else
        echo "supervisord not running (stale PID file)"
        rm -f "$SUPERVISOR_DIR/supervisord.pid"
        rm -f "$SUPERVISOR_DIR/supervisor.sock"
    fi
else
    echo "supervisord not running"
fi
