#!/usr/bin/env bash
# Wrapper for lyra hub daemon — sources .env before launching.
# Supervisor env wins over .env — see run_adapter.sh for rationale (#689).
_sv_snapshot=$(env | grep -E '^(NATS_|LYRA_)' || true)
set -a
[ -f "$HOME/projects/lyra/.env" ] && source "$HOME/projects/lyra/.env"
set +a
while IFS= read -r kv; do [ -n "$kv" ] && export "$kv"; done <<< "$_sv_snapshot"
unset _sv_snapshot
if [ -n "${NATS_NKEY_SEED_PATH:-}" ] && [ ! -r "$NATS_NKEY_SEED_PATH" ]; then
  echo "run_hub.sh: NATS_NKEY_SEED_PATH set but not readable: $NATS_NKEY_SEED_PATH" >&2
  exit 1
fi
exec "$HOME/projects/lyra/.venv/bin/lyra" hub
