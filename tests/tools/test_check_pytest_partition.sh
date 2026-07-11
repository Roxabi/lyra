#!/usr/bin/env bash
# test_check_pytest_partition.sh — partition gate + ci-pytest wrapper contracts.
# Exit 0 = all cases pass.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE="$REPO/tools/check_pytest_partition.py"
WRAPPER="$REPO/scripts/ci-pytest.sh"
EMIT=(env PYTHONPATH=src uv run python "$REPO/tools/pytest_partitions.py")

fail() { echo "TEST FAIL: $1" >&2; exit 1; }

[ -f "$GATE" ] || fail "missing gate script: $GATE"
[ -f "$WRAPPER" ] || fail "missing wrapper: $WRAPPER"

# 1. Gate passes on the in-lockstep repo tree.
rc=0
PYTHONPATH=src uv run python "$GATE" >/dev/null || rc=$?
[ "$rc" -eq 0 ] || fail "gate should pass on the in-lockstep repo tree (exit $rc)"

# 2. emit unknown partition → exit 2 (does not print args).
rc=0
out="$("${EMIT[@]}" emit not_a_real_partition 2>/dev/null)" || rc=$?
[ "$rc" -eq 2 ] || fail "emit unknown should exit 2 (got $rc)"
[ -z "$out" ] || fail "emit unknown should produce no stdout"

# 3. emit known partition → non-empty argv starting with a path.
out="$("${EMIT[@]}" emit factory_unit)"
[ -n "$out" ] || fail "emit factory_unit should produce args"
first="$(printf '%s\n' "$out" | head -1)"
[ "$first" = "tests/" ] || fail "emit factory_unit first arg should be tests/ (got $first)"

# 4. Wrapper: missing partition arg → non-zero.
rc=0
bash "$WRAPPER" >/dev/null 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "ci-pytest.sh with no args should fail"

# 5. Wrapper: unknown partition → exit 2, fail-closed (must not start full suite).
#    Bound wall time so a regression that runs default testpaths fails this case.
tmp_out="$(mktemp)"
tmp_err="$(mktemp)"
trap 'rm -f "$tmp_out" "$tmp_err"' EXIT
rc=0
if command -v timeout >/dev/null 2>&1; then
  timeout 30s bash "$WRAPPER" not_a_real_partition --collect-only -q \
    >"$tmp_out" 2>"$tmp_err" || rc=$?
else
  bash "$WRAPPER" not_a_real_partition --collect-only -q \
    >"$tmp_out" 2>"$tmp_err" || rc=$?
fi
# timeout returns 124 — treat as fail (should never need 30s for emit fail-closed)
[ "$rc" -eq 124 ] && fail "ci-pytest.sh unknown partition timed out (likely full suite)"
[ "$rc" -eq 2 ] || fail "ci-pytest.sh unknown partition should exit 2 (got $rc)"
if grep -qE '::test_|collected [1-9]' "$tmp_out" 2>/dev/null; then
  fail "ci-pytest.sh unknown partition must not collect real tests"
fi
grep -q 'unknown partition\|ERROR' "$tmp_err" \
  || fail "ci-pytest.sh unknown partition should report ERROR on stderr"

echo "check_pytest_partition: all cases pass"
