#!/usr/bin/env bash
# CI wrapper — implementation: scripts/check_inbox_prefix.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run python "${ROOT}/scripts/check_inbox_prefix.py" "$@"