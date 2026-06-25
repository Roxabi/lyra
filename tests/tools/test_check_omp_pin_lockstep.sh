#!/usr/bin/env bash
# test_check_omp_pin_lockstep.sh — verify the #1873 gate passes in-lockstep and
# fails (exit 1) on a mutated digest/version, and breaks (exit 2) on a missing
# file. Uses OMP_PIN_ROOT to point the gate at throwaway fixture trees.
# Exit 0 = all cases pass.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE="$REPO/tools/check_omp_pin_lockstep.sh"

fail() { echo "TEST FAIL: $1" >&2; exit 1; }

# Mirror the three pinned files into an isolated fixture root.
fixture() {
  local dir="$1"
  mkdir -p "$dir/deploy/omp-base" "$dir/src/factory/adapters/omp"
  cp "$REPO/deploy/omp-base/Containerfile" "$dir/deploy/omp-base/Containerfile"
  cp "$REPO/Dockerfile" "$dir/Dockerfile"
  cp "$REPO/src/factory/adapters/omp/_rpc_digest.py" \
     "$dir/src/factory/adapters/omp/_rpc_digest.py"
}

run() {  # run <root>; echoes the gate's exit code without tripping set -e
  local rc=0
  OMP_PIN_ROOT="$1" bash "$GATE" >/dev/null 2>&1 || rc=$?
  printf '%s' "$rc"
}

# 1. The real repo tree is in lockstep → exit 0.
[ "$(run "$REPO")" -eq 0 ] || fail "gate should pass on the in-lockstep repo tree"

# 2. Mutated runtime digest → exit 1.
t="$(mktemp -d)"; fixture "$t"
sed -i 's/_PINNED_SHA256 = "[0-9a-f]*"/_PINNED_SHA256 = "'"$(printf '0%.0s' {1..64})"'"/' \
  "$t/src/factory/adapters/omp/_rpc_digest.py"
[ "$(run "$t")" -eq 1 ] || fail "gate should exit 1 on digest mismatch"
rm -rf "$t"

# 3. Mutated carrier version → exit 1.
t="$(mktemp -d)"; fixture "$t"
sed -i 's/OMP_VERSION=v[0-9.]*/OMP_VERSION=v0.0.0/' "$t/deploy/omp-base/Containerfile"
[ "$(run "$t")" -eq 1 ] || fail "gate should exit 1 on version mismatch"
rm -rf "$t"

# 4. Missing pinned file → exit 2 (script broke, not a violation).
t="$(mktemp -d)"; fixture "$t"; rm "$t/Dockerfile"
[ "$(run "$t")" -eq 2 ] || fail "gate should exit 2 when a pinned file is missing"
rm -rf "$t"

echo "check_omp_pin_lockstep: all cases pass"
