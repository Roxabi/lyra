#!/usr/bin/env bash
# Wrapper for lyra daemon — sources .env before launching.
# supervisor conf points to this script so secrets never live in conf files.
set -a
[ -f "$HOME/projects/lyra/.env" ] && source "$HOME/projects/lyra/.env"
set +a
exec "$HOME/projects/lyra/.venv/bin/python" -m lyra "$@"
