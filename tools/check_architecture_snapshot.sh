#!/usr/bin/env bash
# check_architecture_snapshot.sh — regenerate + diff the architecture snapshot.
#
# Exit-code contract:
#   0 = clean (no drift)
#   1 = drift detected (file modified after regen)
#   2 = generator crash (missing .importlinter / acl-matrix.json / quadlet.toml)
#
# Usage: bash tools/check_architecture_snapshot.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

uv run python "${REPO_ROOT}/tools/generate_architecture_snapshot.py" "${REPO_ROOT}" || exit 2

if ! git -C "${REPO_ROOT}" diff --exit-code docs/architecture/CURRENT.generated.md; then
    echo "::error::CURRENT.generated.md is stale. Run 'python tools/generate_architecture_snapshot.py .' and commit."
    exit 1
fi
