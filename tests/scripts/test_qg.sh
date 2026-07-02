#!/usr/bin/env bash
# Smoke tests for scripts/qg — runner orchestration (not individual gate logic).
#
# Verifies:
#   A. A known-good gate passes on the real repo (duplicate_test_basenames).
#   B. One failing gate does not mark later gates as failed (rc reset per iteration).
#   C. Invalid files regex returns exit 2 with a clear error.
#   D. run_order includes every gate listed in quality_gates.stages.
#   E. CI files: filtering via QG_DIFF_RANGE — skip on non-matching diff, run on
#      matching diff, fail-open without a range / on a bad range / stay filtered
#      on an empty diff.
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
# D. run_order must include every gate listed in quality_gates.stages
# ---------------------------------------------------------------------------
STACK_FILE="${REPO_ROOT}/.claude/stack.yml"
missing=0
while IFS=$'\t' read -r gate stage; do
  [[ -n "$gate" && -n "$stage" ]] || continue
  if ! yq -e ".qg.run_order[\"${stage}\"][] | select(. == \"${gate}\")" "$STACK_FILE" >/dev/null 2>&1; then
    fail "run_order sync" "${gate} has stages=[${stage}] but is missing from qg.run_order.${stage}"
    missing=$((missing + 1))
  fi
done < <(
  yq -r '
    .quality_gates | to_entries | .[] |
    select(.value.enabled != false) |
    select(.value.stages != null or .value.stage != null) |
    .key as $name |
    (.value.stages // [.value.stage] | unique | .[]) as $stage |
    [$name, $stage] | @tsv
  ' "$STACK_FILE"
)

if [[ "$missing" -eq 0 ]]; then
  pass "every quality_gates.stages entry appears in qg.run_order"
fi

# ---------------------------------------------------------------------------
# E. CI files: filtering via QG_DIFF_RANGE — temp fixture git repo
# ---------------------------------------------------------------------------
WORK3="$(mktemp -d)"
mkdir -p "$WORK3/.claude"
cat >"$WORK3/.claude/stack.yml" <<'EOF'
quality_gates:
  filtered_gate:
    enabled: true
    script: echo ran_filtered_marker
    stages: [ci]
    files: ^match/
  unfiltered_gate:
    enabled: true
    script: echo ran_unfiltered_marker
    stages: [ci]
qg:
  run_order:
    ci:
      - filtered_gate
      - unfiltered_gate
EOF

git init "$WORK3" >/dev/null 2>&1
git -C "$WORK3" config user.email "tester@example.com"
git -C "$WORK3" config user.name "tester"
echo base >"$WORK3/other.txt"
git -C "$WORK3" add other.txt
git -C "$WORK3" commit -m base >/dev/null 2>&1
BASE_SHA="$(git -C "$WORK3" rev-parse HEAD)"
echo change >"$WORK3/nomatch.txt"
git -C "$WORK3" add nomatch.txt
git -C "$WORK3" commit -m nomatch >/dev/null 2>&1

run_ci_fixture() {
  # $1 = QG_DIFF_RANGE value ("" → unset)
  local range="$1"
  set +e
  if [[ -n "$range" ]]; then
    ci_out="$(
      QG_REPO_ROOT="$WORK3" QG_STACK="$WORK3/.claude/stack.yml" QG_DIFF_RANGE="$range" \
        "$QG" run --stage ci 2>&1
    )"
  else
    ci_out="$(
      QG_REPO_ROOT="$WORK3" QG_STACK="$WORK3/.claude/stack.yml" \
        "$QG" run --stage ci 2>&1
    )"
  fi
  ci_rc=$?
  set -e
}

# E1. Valid range, diff does NOT match filter → filtered gate skips, unfiltered runs
run_ci_fixture "${BASE_SHA}...HEAD"
if [[ "$ci_rc" -eq 0 ]] \
  && printf '%s\n' "$ci_out" | grep -q 'filtered_gate (skipped — no matching files)' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_unfiltered_marker' \
  && ! printf '%s\n' "$ci_out" | grep -q 'ran_filtered_marker'; then
  pass "ci + QG_DIFF_RANGE + non-matching diff: filtered gate skips, unfiltered runs"
else
  fail "ci non-matching diff" "rc=$ci_rc output: $ci_out"
fi

# E2. No QG_DIFF_RANGE → fail-open, filtered gate runs
run_ci_fixture ""
if [[ "$ci_rc" -eq 0 ]] \
  && printf '%s\n' "$ci_out" | grep -q 'ran_filtered_marker' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_unfiltered_marker'; then
  pass "ci without QG_DIFF_RANGE: fail-open, all gates run"
else
  fail "ci no range" "rc=$ci_rc output: $ci_out"
fi

# E3. Invalid range → fail-open with warning, all gates run, exit 0
run_ci_fixture "definitely-not-a-ref...HEAD"
if [[ "$ci_rc" -eq 0 ]] \
  && printf '%s\n' "$ci_out" | grep -q '::warning::' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_filtered_marker' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_unfiltered_marker'; then
  pass "ci + invalid QG_DIFF_RANGE: fail-open with ::warning::, all gates run"
else
  fail "ci invalid range" "rc=$ci_rc output: $ci_out"
fi

# E4. Valid range, EMPTY diff → filtered gate skips (nothing matched), unfiltered runs
run_ci_fixture "HEAD...HEAD"
if [[ "$ci_rc" -eq 0 ]] \
  && printf '%s\n' "$ci_out" | grep -q 'filtered_gate (skipped — no matching files)' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_unfiltered_marker' \
  && ! printf '%s\n' "$ci_out" | grep -q '::warning::'; then
  pass "ci + empty diff: filtered gate skips, unfiltered runs, no fail-open warning"
else
  fail "ci empty diff" "rc=$ci_rc output: $ci_out"
fi

# E5. Valid range, diff DOES match filter → filtered gate runs
mkdir -p "$WORK3/match"
echo hit >"$WORK3/match/hit.txt"
git -C "$WORK3" add match/hit.txt
git -C "$WORK3" commit -m match >/dev/null 2>&1
run_ci_fixture "${BASE_SHA}...HEAD"
if [[ "$ci_rc" -eq 0 ]] \
  && printf '%s\n' "$ci_out" | grep -q 'ran_filtered_marker' \
  && printf '%s\n' "$ci_out" | grep -q 'ran_unfiltered_marker'; then
  pass "ci + QG_DIFF_RANGE + matching diff: filtered gate runs"
else
  fail "ci matching diff" "rc=$ci_rc output: $ci_out"
fi

rm -rf "$WORK3"

# ---------------------------------------------------------------------------
echo "----"
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
