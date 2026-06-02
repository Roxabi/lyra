#!/usr/bin/env bash
# Smoke test for deploy/factory-gh/hooks/prepare-commit-msg.
#
# Sets up a temp git repo, installs the hook, and verifies:
#   A. Both env vars set → trailers appended exactly once.
#   B. --amend with env vars set → trailer count still exactly 1 each.
#   C. No env vars set → no trailers appended.
#   D. Only FACTORY_AGENT set (FACTORY_SESSION_ID unset) → no trailers appended.
#
# Usage: bash tests/deploy/test_prepare_commit_msg.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); exit 1; }

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        pass "$label"
    else
        fail "$label" "expected to find '$needle' in output; got: $haystack"
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        fail "$label" "expected NOT to find '$needle' in output; got: $haystack"
    else
        pass "$label"
    fi
}

assert_count() {
    local label="$1" haystack="$2" needle="$3" expected="$4"
    local actual
    actual=$(echo "$haystack" | grep -cF "$needle" || true)
    if [ "$actual" -eq "$expected" ]; then
        pass "$label"
    else
        fail "$label" "expected $expected occurrence(s) of '$needle', got $actual; output: $haystack"
    fi
}

# ---------------------------------------------------------------------------
# Resolve hook path relative to the repo root (works regardless of cwd)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK_SRC="$REPO_ROOT/deploy/factory-gh/hooks/prepare-commit-msg"

if [ ! -f "$HOOK_SRC" ]; then
    echo "ERROR: hook not found at $HOOK_SRC" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Temp dir + cleanup
# ---------------------------------------------------------------------------

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

REPO="$TMPDIR_WORK/test-repo"
git init "$REPO" >/dev/null 2>&1
git -C "$REPO" config user.name "tester"
git -C "$REPO" config user.email "tester@x"

# Install hook
mkdir -p "$REPO/.git/hooks"
cp "$HOOK_SRC" "$REPO/.git/hooks/prepare-commit-msg"
chmod +x "$REPO/.git/hooks/prepare-commit-msg"

# ---------------------------------------------------------------------------
# Case A — both vars set: trailers appear exactly once
# ---------------------------------------------------------------------------

(
    cd "$REPO"
    FACTORY_SESSION_ID=abc FACTORY_AGENT=agent-X git commit --allow-empty -m "msg-A"
)

TRAILERS_A="$(git -C "$REPO" log -1 --format="%(trailers)")"

assert_contains "A: Lyra-Session-Id trailer present" "$TRAILERS_A" "Lyra-Session-Id: abc"
assert_contains "A: Lyra-Agent trailer present" "$TRAILERS_A" "Lyra-Agent: agent-X"
assert_count "A: Lyra-Session-Id appears exactly once" "$TRAILERS_A" "Lyra-Session-Id" 1
assert_count "A: Lyra-Agent appears exactly once" "$TRAILERS_A" "Lyra-Agent" 1

# ---------------------------------------------------------------------------
# Case B — --amend with same env: trailer count stays 1 (idempotent)
# ---------------------------------------------------------------------------

(
    cd "$REPO"
    FACTORY_SESSION_ID=abc FACTORY_AGENT=agent-X git commit --allow-empty --amend --no-edit
)

TRAILERS_B="$(git -C "$REPO" log -1 --format="%(trailers)")"

assert_count "B: Lyra-Session-Id still exactly once after --amend" "$TRAILERS_B" "Lyra-Session-Id" 1
assert_count "B: Lyra-Agent still exactly once after --amend" "$TRAILERS_B" "Lyra-Agent" 1

# ---------------------------------------------------------------------------
# Case C — no env vars: no trailers appended
# ---------------------------------------------------------------------------

(
    cd "$REPO"
    unset FACTORY_SESSION_ID FACTORY_AGENT || true
    git commit --allow-empty -m "no env"
)

TRAILERS_C="$(git -C "$REPO" log -1 --format="%(trailers)")"

assert_not_contains "C: no Lyra-Session-Id trailer" "$TRAILERS_C" "Lyra-Session-Id"
assert_not_contains "C: no Lyra-Agent trailer" "$TRAILERS_C" "Lyra-Agent"

# ---------------------------------------------------------------------------
# Case D — only FACTORY_AGENT set, FACTORY_SESSION_ID unset: hook is no-op
# ---------------------------------------------------------------------------

(
    cd "$REPO"
    unset FACTORY_SESSION_ID || true
    FACTORY_AGENT=agent-X git commit --allow-empty -m "partial env"
)

TRAILERS_D="$(git -C "$REPO" log -1 --format="%(trailers)")"

assert_not_contains "D: no Lyra-Session-Id trailer (partial env)" "$TRAILERS_D" "Lyra-Session-Id"
assert_not_contains "D: no Lyra-Agent trailer (partial env)" "$TRAILERS_D" "Lyra-Agent"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
