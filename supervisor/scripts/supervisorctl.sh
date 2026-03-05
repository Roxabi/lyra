#!/bin/bash
# Helper script to run supervisorctl with correct socket path
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$SUPERVISOR_DIR")"

# Activate venv for supervisorctl
source "$PROJECT_DIR/.venv/bin/activate"

exec supervisorctl -c "$SUPERVISOR_DIR/supervisord.conf" "$@"
