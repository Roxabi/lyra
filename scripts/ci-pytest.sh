#!/usr/bin/env bash
# ci-pytest.sh — run a named CI pytest partition (SSoT: tools/pytest_partitions.py).
#
# Usage:
#   scripts/ci-pytest.sh <partition> [extra pytest args...]
#
# Partitions: factory_unit, factory_infra, factory_integration,
#             roxabi_nats, roxabi_contracts, roxabi_obs
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

partition="${1:?usage: ci-pytest.sh <partition> [extra pytest args...]}"
shift

# Process substitution does not surface the child's exit under set -e.
# Write emit output to a temp file so a non-zero emit status aborts before pytest.
# Capture emit_rc explicitly: after `if ! cmd`, $? is 0 (the if test succeeded).
emit_tmp="$(mktemp)"
trap 'rm -f "$emit_tmp"' EXIT
set +e
PYTHONPATH=src uv run python tools/pytest_partitions.py emit "$partition" >"$emit_tmp"
emit_rc=$?
set -e
if [ "$emit_rc" -ne 0 ]; then
  exit "$emit_rc"
fi
mapfile -t BASE_ARGS <"$emit_tmp"
if [ "${#BASE_ARGS[@]}" -eq 0 ]; then
  echo "ERROR: emit produced no pytest args for partition '$partition'" >&2
  exit 2
fi

exec uv run pytest "${BASE_ARGS[@]}" "$@"
