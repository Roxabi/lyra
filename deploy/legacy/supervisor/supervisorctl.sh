#!/bin/bash
# Run supervisorctl against the lyra supervisor socket
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SUPERVISOR_DIR="$SCRIPT_DIR"

exec "$HOME/.local/bin/supervisorctl" -c "$SUPERVISOR_DIR/supervisord.conf" "$@"
