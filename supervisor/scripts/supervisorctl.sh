#!/bin/bash
LYRA_STACK_DIR="${LYRA_STACK_DIR:-$HOME/projects/lyra-stack}"
exec supervisorctl -c "$LYRA_STACK_DIR/supervisord.conf" "$@"
