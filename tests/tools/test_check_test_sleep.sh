#!/usr/bin/env bash
# Smoke tests for tools/check_test_sleep.sh.
#
# Verifies:
#   A. No sleep() calls               → exit 0 (clean).
#   B. sleep() WITH "# event-based"   → exempted, exit 0.
#   C. sleep() WITH "# NATS delivery window" → exempted, exit 0.
#   D. sleep() WITHOUT a sync comment → flagged, exit 1.
#   E. sleep on a mock/patch line     → skipped (not a real sleep), exit 0.
#
# Cases B/C/E are the critical negative tests: they prove the comment-exemption
# and mock-skip guards prevent false positives. Delete a guard → a case breaks → CI catches it.
#
# Usage: bash tests/tools/test_check_test_sleep.sh
set -euo pipefail

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GATE="$REPO_ROOT/tools/check_test_sleep.sh"

if [ ! -f "$GATE" ]; then
    echo "ERROR: gate not found at $GATE" >&2
    exit 1
fi

# Temp git repo (the gate resolves repo root via git) + a tests/ tree to scan.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/repo"
git init "$REPO" >/dev/null 2>&1
git -C "$REPO" config user.name "tester"
git -C "$REPO" config user.email "tester@example.com"
mkdir -p "$REPO/tests"

run_gate() { (cd "$REPO" && bash "$GATE" >/dev/null 2>&1; echo $?); }

# --- Case A — no sleeps → exit 0 ---
cat > "$REPO/tests/test_a.py" << 'PYEOF'
def test_noop():
    assert True
PYEOF
EXIT_A=$(run_gate)
[ "$EXIT_A" -eq 0 ] && pass "A: no sleeps → exit 0" || fail "A: no sleeps → exit 0" "got $EXIT_A"

# --- Case B — sleep with '# event-based' → exempted, exit 0 ---
cat > "$REPO/tests/test_b.py" << 'PYEOF'
import asyncio


async def test_b():
    await asyncio.sleep(0)  # event-based
PYEOF
EXIT_B=$(run_gate)
[ "$EXIT_B" -eq 0 ] && pass "B: sleep + '# event-based' → exit 0" || fail "B: sleep + '# event-based' → exit 0" "got $EXIT_B"
rm -f "$REPO/tests/test_b.py"

# --- Case C — sleep with '# NATS delivery window' → exempted, exit 0 ---
cat > "$REPO/tests/test_c.py" << 'PYEOF'
import asyncio


async def test_c():
    await asyncio.sleep(0.1)  # NATS delivery window
PYEOF
EXIT_C=$(run_gate)
[ "$EXIT_C" -eq 0 ] && pass "C: sleep + '# NATS delivery window' → exit 0" || fail "C: sleep + '# NATS delivery window' → exit 0" "got $EXIT_C"
rm -f "$REPO/tests/test_c.py"

# --- Case D — raw sleep without comment → flagged, exit 1 ---
cat > "$REPO/tests/test_d.py" << 'PYEOF'
import time


def test_d():
    time.sleep(1)
PYEOF
EXIT_D=$(run_gate)
[ "$EXIT_D" -eq 1 ] && pass "D: raw sleep → flagged, exit 1" || fail "D: raw sleep → flagged, exit 1" "got $EXIT_D"
rm -f "$REPO/tests/test_d.py"

# --- Case E — mock/patch sleep line → skipped, exit 0 ---
cat > "$REPO/tests/test_e.py" << 'PYEOF'
from unittest.mock import AsyncMock


def test_e():
    obj.send = AsyncMock(side_effect=lambda *_: asyncio.sleep(1))
PYEOF
EXIT_E=$(run_gate)
[ "$EXIT_E" -eq 0 ] && pass "E: mock sleep line → skipped, exit 0" || fail "E: mock sleep line → skipped, exit 0" "got $EXIT_E"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
