#!/usr/bin/env bash
# test_check_pytest_partition.sh — smoke-test the CI partition gate on HEAD.
# Exit 0 = gate passes on the in-lockstep repo tree.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE="$REPO/tools/check_pytest_partition.py"

fail() { echo "TEST FAIL: $1" >&2; exit 1; }

[ -f "$GATE" ] || fail "missing gate script: $GATE"

rc=0
PYTHONPATH=src uv run python "$GATE" >/dev/null || rc=$?
[ "$rc" -eq 0 ] || fail "gate should pass on the in-lockstep repo tree (exit $rc)"

echo "check_pytest_partition: all cases pass"
