#!/bin/bash
# Run supervisorctl against the lyra deploy supervisor socket
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$(cd "$SCRIPT_DIR/../../deploy/supervisor" && pwd)"

exec "$HOME/.local/bin/supervisorctl" -c "$SUPERVISOR_DIR/supervisord.conf" "$@"
