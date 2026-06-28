#!/usr/bin/env bash
# check_workflows_snapshot.sh — regenerate + diff workflow map JSON.
#
# Exit-code contract:
#   0 = clean (no drift)
#   1 = drift detected (file modified after regen)
#   2 = generator crash
#
# Usage: bash tools/check_workflows_snapshot.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

uv run python "${REPO_ROOT}/tools/generate_workflows.py" "${REPO_ROOT}" || exit 2

if ! git -C "${REPO_ROOT}" diff --exit-code docs/workflows/flows.json; then
    echo "::error::docs/workflows/flows.json is stale. Run 'uv run python tools/generate_workflows.py .' and commit."
    exit 1
fi