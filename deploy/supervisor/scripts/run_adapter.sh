#!/usr/bin/env bash
# Wrapper for lyra adapter daemon — sources .env before launching.
# Usage: run_adapter.sh telegram|discord|stt|tts
#
# IMPORTANT: .env values must NOT override per-program supervisor env.
# We snapshot supervisor-set vars, source .env for shared defaults, then
# restore the snapshot so supervisor conf (e.g. NATS_NKEY_SEED_PATH per
# program) wins. Prevents the regression where a global .env pinned every
# adapter to hub.seed (see #689 cutover investigation).
_sv_snapshot=$(env | grep -E '^(NATS_|LYRA_)' || true)
set -a
[ -f "$HOME/projects/lyra/.env" ] && source "$HOME/projects/lyra/.env"
set +a
while IFS= read -r kv; do [ -n "$kv" ] && export "$kv"; done <<< "$_sv_snapshot"
unset _sv_snapshot
exec "$HOME/projects/lyra/.venv/bin/lyra" adapter "$@"
