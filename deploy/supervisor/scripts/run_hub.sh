#!/usr/bin/env bash
# Wrapper for lyra hub daemon — sources .env before launching.
set -a
[ -f "$HOME/projects/lyra/.env" ] && source "$HOME/projects/lyra/.env"
set +a
exec "$HOME/projects/lyra/.venv/bin/lyra" hub
