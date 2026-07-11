#!/usr/bin/env bash
# test_check_pytest_dir_markers.sh — smoke the directory↔marker gate on HEAD.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE="$REPO/tools/check_pytest_dir_markers.py"

fail() { echo "TEST FAIL: $1" >&2; exit 1; }

[ -f "$GATE" ] || fail "missing gate: $GATE"

rc=0
PYTHONPATH=src uv run python "$GATE" >/dev/null || rc=$?
[ "$rc" -eq 0 ] || fail "gate should pass on the in-lockstep repo tree (exit $rc)"

echo "check_pytest_dir_markers: all cases pass"
