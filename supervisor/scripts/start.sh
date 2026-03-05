#!/bin/bash
# Start supervisord and lyra
set -e

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$SUPERVISOR_DIR")"

# Check if a dev instance is already running
DEV_PIDS=$(pgrep -f "python -m lyra" 2>/dev/null || true)
if [ -n "$DEV_PIDS" ]; then
    SUPERVISOR_PID=""
    if [ -f "$SUPERVISOR_DIR/supervisord.pid" ]; then
        SUPERVISOR_PID=$(cat "$SUPERVISOR_DIR/supervisord.pid" 2>/dev/null || true)
    fi

    for PID in $DEV_PIDS; do
        PARENT_PID=$(ps -o ppid= -p "$PID" 2>/dev/null | tr -d ' ' || true)
        if [ -n "$PARENT_PID" ] && [ "$PARENT_PID" != "$SUPERVISOR_PID" ] && [ "$PARENT_PID" != "1" ]; then
            echo "Error: Lyra dev instance already running (PID: $PID)"
            echo "Stop it first with: kill $PID"
            exit 1
        fi
    done
fi

# Create logs directory if needed
mkdir -p "$SUPERVISOR_DIR/logs"

# Activate virtual environment
source "$PROJECT_DIR/.venv/bin/activate"

# Check if supervisord is already running
SUPERVISOR_RUNNING=false
if [ -f "$SUPERVISOR_DIR/supervisord.pid" ]; then
    PID=$(cat "$SUPERVISOR_DIR/supervisord.pid")
    if kill -0 "$PID" 2>/dev/null; then
        SUPERVISOR_RUNNING=true
        echo "✓ supervisord already running (PID: $PID)"
    else
        echo "Stale PID file found, removing..."
        rm -f "$SUPERVISOR_DIR/supervisord.pid"
        rm -f "$SUPERVISOR_DIR/supervisor.sock"
    fi
fi

# Start supervisord if not running
if [ "$SUPERVISOR_RUNNING" = false ]; then
    echo "Starting supervisord..."
    supervisord -c "$SUPERVISOR_DIR/supervisord.conf"
    sleep 1
    echo "✓ supervisord started"
fi

sleep 1

# Check lyra status and start if not running
BOT_STATUS=$("$SCRIPT_DIR/supervisorctl.sh" status lyra 2>/dev/null || true)
if echo "$BOT_STATUS" | grep -qE "RUNNING|STARTING"; then
    echo "✓ lyra running"
else
    echo "Starting lyra..."
    "$SCRIPT_DIR/supervisorctl.sh" start lyra 2>/dev/null || true
    sleep 1
fi

# Show final status
echo ""
"$SCRIPT_DIR/supervisorctl.sh" status
echo ""
echo "Commands:"
echo "  make lyra          — start"
echo "  make lyra status   — status"
echo "  make lyra logs     — tail logs"
echo "  make lyra reload   — restart"
echo "  make lyra stop     — stop"
