#!/usr/bin/env bash
# Smoke tests for tools/check_debt_expiry.sh.
#
# Verifies:
#   A. No DEBT: markers → exit 0 (clean repo).
#   B. Stale marker WITH issue ref (#NNN) → exempted, exit 0.
#   C. Stale marker WITHOUT issue ref → flagged, exit 1.
#
# Case B is the critical negative test: it proves the issue-ref exemption guard
# (grep -qE '#[0-9]{1,6}') correctly prevents false positives. If the guard were
# deleted, Case B would break and CI would catch it.
#
# Usage: bash tests/tools/test_check_debt_expiry.sh
set -euo pipefail

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Resolve script path
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GATE="$REPO_ROOT/tools/check_debt_expiry.sh"

if [ ! -f "$GATE" ]; then
    echo "ERROR: gate not found at $GATE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Temp git repo + cleanup
# ---------------------------------------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

REPO="$WORK/repo"
git init "$REPO" >/dev/null 2>&1
git -C "$REPO" config user.name "tester"
git -C "$REPO" config user.email "tester@example.com"

# Make an initial commit so git log works inside REPO
touch "$REPO/.gitkeep"
git -C "$REPO" add .gitkeep
GIT_AUTHOR_DATE="2000-01-01T00:00:00" GIT_COMMITTER_DATE="2000-01-01T00:00:00" \
    git -C "$REPO" commit -m "init" >/dev/null 2>&1

# Source directory that the gate scans (default: src/)
mkdir -p "$REPO/src"

# Helper: run gate inside REPO, capturing exit code without aborting the script
run_gate() {
    (cd "$REPO" && bash "$GATE" 2>&1)
    echo $?
}

# ---------------------------------------------------------------------------
# Case A — no DEBT: markers → exit 0
# ---------------------------------------------------------------------------
EXIT_A=$(cd "$REPO" && bash "$GATE" >/dev/null 2>&1; echo $?)
if [ "$EXIT_A" -eq 0 ]; then
    pass "A: no DEBT: markers → exit 0"
else
    fail "A: no DEBT: markers → exit 0" "got exit $EXIT_A"
fi

# ---------------------------------------------------------------------------
# Case B — stale file with DEBT: + issue ref (#1234) → exempted, exit 0
# ---------------------------------------------------------------------------
cat > "$REPO/src/example.py" << 'PYEOF'
# DEBT:boundary-broad-catch #1234 — tracked by open issue
def foo():
    pass
PYEOF

# Commit with a date far in the past (> 6 months ago)
git -C "$REPO" add src/example.py
GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
    git -C "$REPO" commit -m "add stale file with issue ref" >/dev/null 2>&1

EXIT_B=$(cd "$REPO" && bash "$GATE" >/dev/null 2>&1; echo $?)
if [ "$EXIT_B" -eq 0 ]; then
    pass "B: stale DEBT: with issue ref → exempted, exit 0"
else
    fail "B: stale DEBT: with issue ref → exempted, exit 0" "got exit $EXIT_B (guard missing or broken)"
fi

# ---------------------------------------------------------------------------
# Case C — stale file with DEBT: without issue ref → flagged, exit 1
# ---------------------------------------------------------------------------
cat > "$REPO/src/example.py" << 'PYEOF'
# DEBT:boundary-broad-catch — no issue ref, will expire
def foo():
    pass
PYEOF

git -C "$REPO" add src/example.py
GIT_AUTHOR_DATE="2020-01-01T00:00:00" GIT_COMMITTER_DATE="2020-01-01T00:00:00" \
    git -C "$REPO" commit -m "stale marker without issue ref" >/dev/null 2>&1

EXIT_C=$(cd "$REPO" && bash "$GATE" >/dev/null 2>&1; echo $?)
if [ "$EXIT_C" -eq 1 ]; then
    pass "C: stale DEBT: without issue ref → flagged, exit 1"
else
    fail "C: stale DEBT: without issue ref → flagged, exit 1" "got exit $EXIT_C"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
