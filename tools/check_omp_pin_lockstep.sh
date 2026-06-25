#!/usr/bin/env bash
# check_omp_pin_lockstep.sh — assert the omp pin trio stays in lockstep (#1873).
#
# A single omp release is pinned in three files; a version bump that updates one
# but not the others ships a runtime DigestMismatchError (the binary the carrier
# downloads no longer matches the sha the adapter re-verifies at startup). This
# gate fails the build before that can land:
#
#   1. deploy/omp-base/Containerfile             — OMP_VERSION (vX.Y.Z) + OMP_SHA256 (binary digest)
#   2. Dockerfile                                — factory-omp-base:X.Y.Z tag (carrier the binary is copied from)
#   3. src/factory/adapters/omp/_rpc_digest.py   — _PINNED_SHA256 (digest re-verified at runtime)
#
# Invariants:
#   A. OMP_SHA256 (Containerfile) == _PINNED_SHA256 (_rpc_digest.py)        [binary digest lockstep]
#   B. OMP_VERSION without 'v' (Containerfile) == factory-omp-base tag (Dockerfile)  [version lockstep]
#
# Note: _rpc_digest.py holds the literal; _rpc_bridge.py only re-exports it (#1873) — grep the literal.
#
# Exit: 0 = in lockstep · 1 = mismatch (gate fails) · 2 = script broke (missing file / pin pattern moved).
set -euo pipefail

ROOT="${OMP_PIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONTAINERFILE="$ROOT/deploy/omp-base/Containerfile"
DOCKERFILE="$ROOT/Dockerfile"
DIGEST_PY="$ROOT/src/factory/adapters/omp/_rpc_digest.py"

for f in "$CONTAINERFILE" "$DOCKERFILE" "$DIGEST_PY"; do
  [ -f "$f" ] || { echo "check_omp_pin_lockstep: missing $f" >&2; exit 2; }
done

# Extract a single value via grep; empty result = the pin pattern moved → exit 2.
grab() {  # grab <egrep-pattern> <file> <strip-prefix>
  local match
  match="$(grep -oE "$1" "$2" | head -1 || true)"
  [ -n "$match" ] || { echo "check_omp_pin_lockstep: pattern $1 not found in $2 (pin moved?)" >&2; exit 2; }
  printf '%s' "${match#$3}"
}

omp_version="$(grab 'OMP_VERSION=v[0-9.]+' "$CONTAINERFILE" 'OMP_VERSION=v')"
omp_sha="$(grab 'OMP_SHA256=[0-9a-f]{64}' "$CONTAINERFILE" 'OMP_SHA256=')"
docker_tag="$(grab 'factory-omp-base:[0-9.]+' "$DOCKERFILE" 'factory-omp-base:')"
pinned_sha="$(grab '_PINNED_SHA256 = "[0-9a-f]{64}"' "$DIGEST_PY" '_PINNED_SHA256 = "')"
pinned_sha="${pinned_sha%\"}"

rc=0
if [ "$omp_sha" != "$pinned_sha" ]; then
  echo "MISMATCH binary digest: omp-base OMP_SHA256=$omp_sha != _rpc_digest _PINNED_SHA256=$pinned_sha" >&2
  rc=1
fi
if [ "$omp_version" != "$docker_tag" ]; then
  echo "MISMATCH omp version: omp-base OMP_VERSION=v$omp_version != Dockerfile factory-omp-base:$docker_tag" >&2
  rc=1
fi

[ "$rc" -eq 0 ] && echo "omp pin lockstep OK — version=$omp_version sha=${omp_sha:0:12}…"
exit "$rc"
