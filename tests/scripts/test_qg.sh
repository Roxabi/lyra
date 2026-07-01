#!/usr/bin/env bash
# Smoke tests for scripts/qg — runner orchestration (not individual gate logic).
#
# Verifies:
#   A. A known-good gate passes on the real repo (duplicate_test_basenames).
#   B. One failing gate does not mark later gates as failed (rc reset per iteration).
#   C. Invalid files regex returns exit 2 with a clear error.
#
# Usage: bash tests/scripts/test_qg.sh

set -euo pipefail

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
QG="${REPO_ROOT}/scripts/qg"

if [[ ! -x "$QG" ]]; then
  echo "ERROR: qg runner not found at $QG" >&2
  exit 1
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: yq required for test_qg.sh" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# A. Smoke — real repo, single gate
# ---------------------------------------------------------------------------
if "$QG" run duplicate_test_basenames >/dev/null 2>&1; then
  pass "duplicate_test_basenames passes on HEAD"
else
  fail "duplicate_test_basenames" "expected exit 0"
fi

# ---------------------------------------------------------------------------
# B. rc reset — temp fixture repo
# ---------------------------------------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/.claude"
cat >"$WORK/.claude/stack.yml" <<'EOF'
quality_gates:
  gate_fail:
    enabled: true
    script: exit 1
    stages: [ci]
  gate_ok:
    enabled: true
    script: true
    stages: [ci]
qg:
  run_order:
    ci:
      - gate_fail
      - gate_ok
EOF

set +e
output="$(
  QG_REPO_ROOT="$WORK" QG_STACK="$WORK/.claude/stack.yml" \
    "$QG" run --stage ci 2>&1
)"
rc=$?
set -e

error_count=$(printf '%s\n' "$output" | grep -c '::error::gate gate_fail failed' || true)
false_ok_errors=$(printf '%s\n' "$output" | grep -c '::error::gate gate_ok failed' || true)

if [[ "$rc" -eq 1 ]]; then
  pass "fixture stage exits 1 when a gate fails"
else
  fail "fixture exit code" "expected 1, got $rc"
fi

if [[ "$error_count" -eq 1 ]]; then
  pass "only gate_fail is reported as failed"
else
  fail "rc reset" "expected 1 error for gate_fail, got $error_count"
fi

if [[ "$false_ok_errors" -eq 0 ]]; then
  pass "gate_ok not falsely reported as failed"
else
  fail "rc reset" "gate_ok incorrectly failed ($false_ok_errors annotations)"
fi

# ---------------------------------------------------------------------------
# C. Invalid files regex — temp fixture
# ---------------------------------------------------------------------------
WORK2="$(mktemp -d)"
mkdir -p "$WORK2/.claude"
cat >"$WORK2/.claude/stack.yml" <<'EOF'
quality_gates:
  bad_regex:
    enabled: true
    script: true
    stages: [pre-commit]
    files: "[unclosed"
qg:
  run_order:
    pre-commit:
      - bad_regex
EOF

git init "$WORK2" >/dev/null 2>&1
git -C "$WORK2" config user.email "tester@example.com"
git -C "$WORK2" config user.name "tester"
echo x >"$WORK2/file.txt"
git -C "$WORK2" add file.txt
git -C "$WORK2" commit -m init >/dev/null 2>&1
echo change >>"$WORK2/file.txt"
git -C "$WORK2" add file.txt

set +e
regex_out="$(
  QG_REPO_ROOT="$WORK2" QG_STACK="$WORK2/.claude/stack.yml" \
    "$QG" run --stage pre-commit 2>&1
)"
regex_rc=$?
set -e

if [[ "$regex_rc" -ne 0 ]] && printf '%s\n' "$regex_out" | grep -q 'invalid files regex'; then
  pass "invalid files regex fails with clear message (exit $regex_rc)"
else
  fail "invalid regex" "expected non-zero exit with message, got $regex_rc"
fi

rm -rf "$WORK2"

# ---------------------------------------------------------------------------
echo "----"
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]