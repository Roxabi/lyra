#!/usr/bin/env bash
# Smoke tests for tools/check_file_exemptions.sh.
#
# Verifies:
#   A. Empty exemptions file (comments only) → exit 0
#   B. Entry with a valid future expires= date → exit 0
#   C. Entry with an expired date → exit 1
#   D. Entry missing expires= entirely → exit 1
#   E. Multiple entries — one expired, one valid → exit 1
#
# Usage: bash tests/tools/test_check_file_exemptions.sh
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
GATE="$REPO_ROOT/tools/check_file_exemptions.sh"

if [ ! -f "$GATE" ]; then
    echo "ERROR: gate not found at $GATE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Temp dir + cleanup
# ---------------------------------------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# We need a git repo so the gate can resolve repo root via git rev-parse.
REPO="$WORK/repo"
git init "$REPO" >/dev/null 2>&1
git -C "$REPO" config user.name "tester"
git -C "$REPO" config user.email "tester@example.com"
touch "$REPO/.gitkeep"
git -C "$REPO" add .gitkeep
git -C "$REPO" commit -m "init" >/dev/null 2>&1

EXEMPTIONS="$REPO/tools/file_exemptions.txt"
mkdir -p "$REPO/tools"

# Fixed "today" so tests are date-stable.
FIXED_TODAY="2026-06-02"
FUTURE="2026-12-02"
PAST="2026-01-01"

# Helper: run gate with controlled today date and a given exemptions content.
run_gate() {
    local content="$1"
    printf '%s\n' "$content" > "$EXEMPTIONS"
    (
        cd "$REPO"
        FILE_EXEMPTIONS_PATH="tools/file_exemptions.txt" \
        FILE_EXEMPTIONS_TODAY="$FIXED_TODAY" \
        bash "$GATE" >/dev/null 2>&1
    )
    echo $?
}

# ---------------------------------------------------------------------------
# Case A — comments-only file → exit 0
# ---------------------------------------------------------------------------
CONTENT_A="$(cat <<'EOF'
# File size exemptions
# Format: <path>  # <N> lines — <issue> <description> expires=YYYY-MM-DD
EOF
)"
EXIT_A=$(run_gate "$CONTENT_A")
if [ "$EXIT_A" -eq 0 ]; then
    pass "A: comments-only → exit 0"
else
    fail "A: comments-only → exit 0" "got exit $EXIT_A"
fi

# ---------------------------------------------------------------------------
# Case B — valid future expires= date → exit 0
# ---------------------------------------------------------------------------
CONTENT_B="src/factory/foo.py  # 350 lines — DEBT:refactor — #1234 rationale expires=${FUTURE}"
EXIT_B=$(run_gate "$CONTENT_B")
if [ "$EXIT_B" -eq 0 ]; then
    pass "B: valid future date → exit 0"
else
    fail "B: valid future date → exit 0" "got exit $EXIT_B"
fi

# ---------------------------------------------------------------------------
# Case C — expired date → exit 1
# ---------------------------------------------------------------------------
CONTENT_C="src/factory/bar.py  # 320 lines — DEBT:old-debt — #999 rationale expires=${PAST}"
EXIT_C=$(run_gate "$CONTENT_C")
if [ "$EXIT_C" -eq 1 ]; then
    pass "C: expired date → exit 1"
else
    fail "C: expired date → exit 1" "got exit $EXIT_C"
fi

# ---------------------------------------------------------------------------
# Case D — missing expires= → exit 1
# ---------------------------------------------------------------------------
CONTENT_D="src/factory/baz.py  # 310 lines — DEBT:missing-date — #777 rationale"
EXIT_D=$(run_gate "$CONTENT_D")
if [ "$EXIT_D" -eq 1 ]; then
    pass "D: missing expires= → exit 1"
else
    fail "D: missing expires= → exit 1" "got exit $EXIT_D"
fi

# ---------------------------------------------------------------------------
# Case E — mixed: one valid + one expired → exit 1
# ---------------------------------------------------------------------------
CONTENT_E="$(cat <<EOF
src/factory/ok.py  # 300 lines — DEBT:fine — #111 rationale expires=${FUTURE}
src/factory/old.py  # 400 lines — DEBT:stale — #222 rationale expires=${PAST}
EOF
)"
EXIT_E=$(run_gate "$CONTENT_E")
if [ "$EXIT_E" -eq 1 ]; then
    pass "E: mixed valid+expired → exit 1"
else
    fail "E: mixed valid+expired → exit 1" "got exit $EXIT_E"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
