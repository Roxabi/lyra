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

mapfile -t BASE_ARGS < <(PYTHONPATH=src uv run python tools/pytest_partitions.py emit "$partition")

exec uv run pytest "${BASE_ARGS[@]}" "$@"
