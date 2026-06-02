#!/usr/bin/env bash
# Smoke tests for tools/check_hardcoded_constants.sh.
#
# Verifies:
#   A. A baselined line passes (exit 0).
#   B. A NEW hardcoded constant (not in baseline) fails (exit 1).
#   C. A line referencing Config.SOMETHING passes (exit 0) — sanctioned indirection.
#   D. An inline-exempt line (# const-ok: <reason>) passes (exit 0).
#   E. The real src/factory/core/ tree passes (exit 0) — proves baseline covers current HEAD.
#
# Cases C and D are the critical negative tests: they prove the exemption guards
# prevent false positives.  Delete a guard → the case breaks → CI catches it.
#
# Fixtures for A–D are written to a temporary git repo so the gate (which calls
# git rev-parse to resolve repo root) works correctly.  Real source is NOT mutated.
#
# Usage: bash tests/tools/test_check_hardcoded_constants.sh

set -euo pipefail

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Resolve script + repo paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GATE="$REPO_ROOT/tools/check_hardcoded_constants.sh"
REAL_BASELINE="$REPO_ROOT/tools/hardcoded_constants_baseline.txt"

if [ ! -f "$GATE" ]; then
    echo "ERROR: gate not found at $GATE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Set up a temp git repo for fixture-based tests (A–D)
# ---------------------------------------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
git init "$REPO" >/dev/null 2>&1
git -C "$REPO" config user.name  "tester"
git -C "$REPO" config user.email "tester@example.com"
touch "$REPO/.gitkeep"
git -C "$REPO" add .gitkeep
git -C "$REPO" commit -m "init" >/dev/null 2>&1

# We write fixtures under src/factory/core/ inside the temp repo so the gate
# scans the right directory.
CORE_DIR="$REPO/src/factory/core"
mkdir -p "$CORE_DIR"

# Baseline file inside the temp repo — we control its contents per test.
BASELINE="$REPO/tools/baseline.txt"
mkdir -p "$REPO/tools"

# Helper: run gate against the temp repo with our controlled baseline.
# CONST_SCAN_ROOT and CONST_BASELINE_FILE override the defaults.
run_gate() {
    (
        cd "$REPO"
        CONST_SCAN_ROOT="src/factory/core/" \
        CONST_BASELINE_FILE="tools/baseline.txt" \
        bash "$GATE" >/dev/null 2>&1
    )
    echo $?
}

# ---------------------------------------------------------------------------
# Case A — a baselined line passes (exit 0)
# ---------------------------------------------------------------------------
# Write a file with a hardcoded constant
cat > "$CORE_DIR/test_a.py" << 'PYEOF'
TIMEOUT = 30
PYEOF

# Put that exact signature in the baseline
printf 'src/factory/core/test_a.py:TIMEOUT = 30\n' > "$BASELINE"

EXIT_A=$(run_gate)
if [ "$EXIT_A" -eq 0 ]; then
    pass "A: baselined line → exit 0"
else
    fail "A: baselined line → exit 0" "got exit $EXIT_A"
fi

# Clean up fixture
rm -f "$CORE_DIR/test_a.py"

# ---------------------------------------------------------------------------
# Case B — NEW hardcoded constant (not in baseline) fails (exit 1)
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_b.py" << 'PYEOF'
MAX_RETRIES = 15
PYEOF

# Empty baseline — no entries grandfathered
printf '# empty baseline\n' > "$BASELINE"

EXIT_B=$(run_gate)
if [ "$EXIT_B" -eq 1 ]; then
    pass "B: new hardcoded constant → exit 1"
else
    fail "B: new hardcoded constant → exit 1" "got exit $EXIT_B"
fi

rm -f "$CORE_DIR/test_b.py"

# ---------------------------------------------------------------------------
# Case C — line referencing Config.SOMETHING passes (exit 0)
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_c.py" << 'PYEOF'
limit = BusConfig.DEFAULT_MAXSIZE
PYEOF

# Empty baseline — the Config. exemption must fire, not the baseline
printf '# empty baseline\n' > "$BASELINE"

EXIT_C=$(run_gate)
if [ "$EXIT_C" -eq 0 ]; then
    pass "C: Config. reference → exit 0 (sanctioned indirection)"
else
    fail "C: Config. reference → exit 0" "got exit $EXIT_C (Config. guard missing or broken)"
fi

rm -f "$CORE_DIR/test_c.py"

# ---------------------------------------------------------------------------
# Case D — inline-exempt line (# const-ok:) passes (exit 0)
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_d.py" << 'PYEOF'
NATS_MAX_PAYLOAD = 1024 * 1024  # const-ok: NATS protocol hard limit, cannot be config
PYEOF

# Empty baseline — the const-ok: exemption must fire
printf '# empty baseline\n' > "$BASELINE"

EXIT_D=$(run_gate)
if [ "$EXIT_D" -eq 0 ]; then
    pass "D: # const-ok: line → exit 0 (inline exempt)"
else
    fail "D: # const-ok: line → exit 0" "got exit $EXIT_D (inline-exempt guard missing or broken)"
fi

rm -f "$CORE_DIR/test_d.py"

# ---------------------------------------------------------------------------
# Case E — real src/factory/core/ tree passes (exit 0)
# Proves the committed baseline grandfathers all constants at current HEAD.
# ---------------------------------------------------------------------------
if [ ! -f "$REAL_BASELINE" ]; then
    fail "E: real tree → exit 0" "baseline file not found at $REAL_BASELINE"
else
    EXIT_E=$(
        cd "$REPO_ROOT"
        bash "$GATE" >/dev/null 2>&1
        echo $?
    )
    if [ "$EXIT_E" -eq 0 ]; then
        pass "E: real src/factory/core/ tree → exit 0"
    else
        fail "E: real src/factory/core/ tree → exit 0" "got exit $EXIT_E (baseline out of sync with HEAD)"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
