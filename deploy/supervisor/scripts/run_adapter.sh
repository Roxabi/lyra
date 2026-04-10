#!/usr/bin/env bash
# Wrapper for lyra adapter daemon — sources .env before launching.
# Usage: run_adapter.sh telegram|discord|stt|tts
set -a
[ -f "$HOME/projects/lyra/.env" ] && source "$HOME/projects/lyra/.env"
set +a
exec "$HOME/projects/lyra/.venv/bin/lyra" adapter "$@"
