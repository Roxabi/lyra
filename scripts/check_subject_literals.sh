#!/usr/bin/env bash
# CI wrapper — implementation: scripts/check_subject_literals.py
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run python "${ROOT}/scripts/check_subject_literals.py" "$@"