#!/usr/bin/env bash
# check_onboarding_smoke_attest.sh — hash-bound attestation gate for the
# container-only onboarding docs (#2201).
#
# The one-shot onboarding smoke (Podman + secrets + a bot that replies on a
# virgin host) is NOT reproducible in CI. Instead, whenever QUICKSTART.md /
# GETTING-STARTED.md / MULTI-BOT.md change, an operator must re-run the smoke
# verbatim and refresh tools/attestations/onboarding-smoke.toml. This gate only
# checks that the attestation's `docs_hash` still matches the current docs
# (pattern: architecture_snapshot / theme_build_drift). It does NOT run the
# smoke — the human attestation (date/operator/notes) carries that guarantee.
#
# On mismatch: red, with the instruction to re-run the smoke and re-attest.
#
# Exit contract (tools/AGENTS.md):
#   0 = clean (hash matches, or --print-hash succeeded)
#   1 = drift (docs changed without a matching attestation)
#   2 = script broke (missing doc/attestation/tool)
#
# Usage:
#   bash tools/check_onboarding_smoke_attest.sh              # gate
#   bash tools/check_onboarding_smoke_attest.sh --print-hash # emit current hash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ATTEST="${REPO_ROOT}/tools/attestations/onboarding-smoke.toml"

# Fixed order — the hash is order-sensitive; keep this list in sync with the
# `files:` filter on the onboarding_smoke_attest gate in .claude/stack.yml.
DOCS=(
  "docs/QUICKSTART.md"
  "docs/GETTING-STARTED.md"
  "docs/MULTI-BOT.md"
)

# Normalized concatenation: per-line trailing-whitespace stripped, one explicit
# newline separator appended after each doc (stable regardless of whether a file
# ends with a newline). sha256 of the stream = docs_hash.
compute_hash() {
  local f
  for f in "${DOCS[@]}"; do
    if [[ ! -f "${REPO_ROOT}/${f}" ]]; then
      echo "onboarding_smoke_attest: missing doc ${f}" >&2
      return 2
    fi
    sed -e 's/[[:space:]]*$//' "${REPO_ROOT}/${f}"
    printf '\n'
  done | sha256sum | awk '{print $1}'
}

if ! command -v sha256sum >/dev/null 2>&1; then
  echo "onboarding_smoke_attest: sha256sum not found on PATH" >&2
  exit 2
fi

current="$(compute_hash)" || exit 2

if [[ "${1:-}" == "--print-hash" ]]; then
  echo "${current}"
  exit 0
fi

if [[ ! -f "${ATTEST}" ]]; then
  echo "onboarding_smoke_attest: attestation not found at ${ATTEST}" >&2
  exit 2
fi

# Extract docs_hash = "…" from the TOML (first match wins).
attested="$(sed -n 's/^[[:space:]]*docs_hash[[:space:]]*=[[:space:]]*"\([0-9a-f]*\)".*/\1/p' "${ATTEST}" | head -n1)"
if [[ -z "${attested}" ]]; then
  echo "onboarding_smoke_attest: no docs_hash field in ${ATTEST}" >&2
  exit 2
fi

if [[ "${current}" != "${attested}" ]]; then
  cat >&2 <<EOF
onboarding_smoke_attest: docs_hash MISMATCH
  attested: ${attested}
  current:  ${current}

One or more onboarding docs (${DOCS[*]}) changed without re-attestation.
Re-run the one-shot onboarding smoke VERBATIM on a virgin host (Podman + bot
secrets + a bot that replies), then update tools/attestations/onboarding-smoke.toml:
  docs_hash = "$(cat <<<"${current}")"
  date/operator/notes = the fresh smoke result (drop the PLACEHOLDER note).
Recompute the hash with: bash tools/check_onboarding_smoke_attest.sh --print-hash
EOF
  exit 1
fi

echo "onboarding_smoke_attest: OK (docs_hash matches attestation)"
exit 0
