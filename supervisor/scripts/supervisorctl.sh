#!/bin/bash
# Helper script to run supervisorctl with correct socket path
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$SUPERVISOR_DIR")"

# Activate venv for supervisorctl
source "$PROJECT_DIR/.venv/bin/activate"

exec supervisorctl -c "$SUPERVISOR_DIR/supervisord.conf" "$@"
