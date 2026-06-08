#!/usr/bin/env bash
# Smoke tests for tools/check_hardcoded_constants.sh.
#
# Verifies:
#   A. A baselined line passes (exit 0).
#   B. A NEW hardcoded constant (not in baseline) fails (exit 1).
#   C. A line referencing Config.SOMETHING passes (exit 0) — sanctioned indirection.
#   D. An inline-exempt line (# const-ok: <reason>) passes (exit 0).
#   E. The real src/factory/core/ tree passes (exit 0) with NO baseline present —
#      proves the burn-down (#1698) left zero flagged constants at HEAD.
#   J. An ABSENT baseline + clean tree → exit 0 (absent = empty set, #1706).
#   K. An ABSENT baseline + a new magic number → exit 1 (#1706).
#
# Cases C and D are the critical negative tests: they prove the exemption guards
# prevent false positives.  Delete a guard → the case breaks → CI catches it.
# Cases J and K pin the #1706 contract: a missing baseline file is the empty set,
# not a hard error (the gate used to exit 2 on a missing baseline).
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

# Helper: run gate against the temp repo with NO baseline file present.
# Points CONST_BASELINE_FILE at a path that does not exist — the gate must
# treat it as the empty set (#1706), never exit 2.
run_gate_no_baseline() {
    rm -f "$REPO/tools/absent-baseline.txt"
    (
        cd "$REPO"
        CONST_SCAN_ROOT="src/factory/core/" \
        CONST_BASELINE_FILE="tools/absent-baseline.txt" \
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
# Case F — f-string format spec width literal is NOT flagged (exit 0)
# Proves the f-string format-spec skip rule fires for {x:<20} style widths.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_f.py" << 'PYEOF'
lines = []
lines.append(f"  {'name':<20} {value}")
state_str = f"{status.upper():<10} (ok)"
PYEOF

# Empty baseline — the f-string skip rule must fire, not the baseline
printf '# empty baseline\n' > "$BASELINE"

EXIT_F=$(run_gate)
if [ "$EXIT_F" -eq 0 ]; then
    pass "F: f-string format spec {x:<20} → exit 0 (f-string skip rule)"
else
    fail "F: f-string format spec {x:<20} → exit 0" "got exit $EXIT_F (f-string skip rule missing or broken)"
fi

rm -f "$CORE_DIR/test_f.py"

# ---------------------------------------------------------------------------
# Case G — regex quantifier preceded by backslash escape class is NOT flagged
# Proves the regex-quantifier skip rule fires for \d{8,12} style patterns.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_g.py" << 'PYEOF'
import re
PATTERN = re.compile(r"(?<!\w)(\d{8,12}:[A-Za-z0-9]+)")
PYEOF

# Empty baseline — the regex-quantifier skip rule must fire
printf '# empty baseline\n' > "$BASELINE"

EXIT_G=$(run_gate)
if [ "$EXIT_G" -eq 0 ]; then
    pass "G: regex quantifier \\d{8,12} → exit 0 (regex-quantifier skip rule)"
else
    fail "G: regex quantifier \\d{8,12} → exit 0" "got exit $EXIT_G (regex-quantifier skip rule missing or broken)"
fi

rm -f "$CORE_DIR/test_g.py"

# ---------------------------------------------------------------------------
# Case H — plain magic number (no marker) is still flagged (exit 1)
# False-negative guard: proves the skip rules did not blind the gate to
# genuine usage-site magic numbers.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_h.py" << 'PYEOF'
_planted = 4096
PYEOF

# Empty baseline — no grandfathering; the gate must catch the literal
printf '# empty baseline\n' > "$BASELINE"

EXIT_H=$(run_gate)
if [ "$EXIT_H" -eq 1 ]; then
    pass "H: plain magic number _planted = 4096 → exit 1 (gate still catches it)"
else
    fail "H: plain magic number _planted = 4096 → exit 1" "got exit $EXIT_H (skip rules over-fired or gate broken)"
fi

rm -f "$CORE_DIR/test_h.py"

# ---------------------------------------------------------------------------
# Case I — co-located real magic number + f-string format spec is flagged
# Regression guard: the old `next`-based rule skipped the WHOLE line, so a
# real literal on the same line as a valid f-string format spec escaped the
# gate.  The gsub-based fix strips the f-string token and then detects the
# remaining literal.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_i.py" << 'PYEOF'
BUFFER_SIZE = 4096; label = f"{name:<20}"
PYEOF

# Empty baseline — gate must flag the 4096 literal (exit 1)
printf '# empty baseline\n' > "$BASELINE"

EXIT_I=$(run_gate)
if [ "$EXIT_I" -eq 1 ]; then
    pass "I: real magic number + f-string format spec on same line → exit 1 (gate flags the literal)"
else
    fail "I: real magic number + f-string format spec on same line → exit 1" "got exit $EXIT_I (gsub fix missing or broken)"
fi

rm -f "$CORE_DIR/test_i.py"

# ---------------------------------------------------------------------------
# Case G3 — Python set literal {10, 20} (no backslash prefix) IS flagged
# Documents that the regex-quantifier strip rule does NOT exempt plain set or
# dict literals — only patterns preceded by a \d/\w/\s escape class are stripped.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_g3.py" << 'PYEOF'
allowed = {10, 20}
PYEOF

# Empty baseline — gate must flag the literals (exit 1)
printf '# empty baseline\n' > "$BASELINE"

EXIT_G3=$(run_gate)
if [ "$EXIT_G3" -eq 1 ]; then
    pass "G3: plain set literal {10, 20} → exit 1 (regex-quantifier rule does NOT exempt it)"
else
    fail "G3: plain set literal {10, 20} → exit 1" "got exit $EXIT_G3 (regex-quantifier strip over-fired — strips set literals too)"
fi

rm -f "$CORE_DIR/test_g3.py"

# ---------------------------------------------------------------------------
# Case J — ABSENT baseline + clean tree → exit 0 (#1706)
# Empty src/factory/core/ (no flagged constants) with no baseline file must be
# treated as the empty set, not a hard error.  Guards the old exit-2 behaviour.
# ---------------------------------------------------------------------------
# Ensure the temp core tree has no flagged constants for this case.
rm -f "$CORE_DIR"/*.py
cat > "$CORE_DIR/test_j.py" << 'PYEOF'
import os  # no numeric literals here
PYEOF

EXIT_J=$(run_gate_no_baseline)
if [ "$EXIT_J" -eq 0 ]; then
    pass "J: absent baseline + clean tree → exit 0 (empty-set semantics)"
else
    fail "J: absent baseline + clean tree → exit 0" "got exit $EXIT_J (absent baseline not treated as empty set)"
fi

rm -f "$CORE_DIR/test_j.py"

# ---------------------------------------------------------------------------
# Case K — ABSENT baseline + new magic number → exit 1 (#1706)
# With no baseline to grandfather it, an un-marked literal is a violation.
# ---------------------------------------------------------------------------
cat > "$CORE_DIR/test_k.py" << 'PYEOF'
POOL_SIZE = 64
PYEOF

EXIT_K=$(run_gate_no_baseline)
if [ "$EXIT_K" -eq 1 ]; then
    pass "K: absent baseline + new magic number → exit 1 (still merge-blocking)"
else
    fail "K: absent baseline + new magic number → exit 1" "got exit $EXIT_K (absent baseline swallowed the violation)"
fi

rm -f "$CORE_DIR/test_k.py"

# ---------------------------------------------------------------------------
# Case E — real src/factory/core/ tree passes (exit 0) with NO baseline
# The grandfather baseline was retired (#1706); the gate runs against the live
# tree with its default (now-absent) baseline path and must come back clean,
# proving the #1698 burn-down left zero flagged constants at HEAD.
# ---------------------------------------------------------------------------
if [ -f "$REAL_BASELINE" ]; then
    fail "E: real tree → exit 0 (no baseline)" "baseline file still present at $REAL_BASELINE — it was meant to be deleted in #1706"
else
    EXIT_E=$(
        cd "$REPO_ROOT"
        bash "$GATE" >/dev/null 2>&1
        echo $?
    )
    if [ "$EXIT_E" -eq 0 ]; then
        pass "E: real src/factory/core/ tree → exit 0 (no baseline, empty-set semantics)"
    else
        fail "E: real src/factory/core/ tree → exit 0 (no baseline)" "got exit $EXIT_E (new un-grandfathered constant at HEAD, or gate error)"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
