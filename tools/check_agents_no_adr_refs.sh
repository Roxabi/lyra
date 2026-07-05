#!/usr/bin/env bash
# check_agents_no_adr_refs.sh — AGENTS.md must not cite ADR-NNN as operational rules.
# Invariants live in domain pages (L1) or .importlinter (L0); ADRs are L3 provenance only.
# EXIT CODES: 0=clean, 1=violations, 2=script error

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || { echo "ERROR: not a git repository" >&2; exit 2; }
cd "$REPO_ROOT"

VIOLATIONS="$(
    git ls-files '**/AGENTS.md' \
    | while IFS= read -r f; do
        grep -En 'ADR-[0-9]+' "$f" 2>/dev/null \
            | sed "s|^|${f}:|" || true
      done
)"

if [ -z "$VIOLATIONS" ]; then
    echo "check_agents_no_adr_refs: no ADR-NNN citations in AGENTS.md — OK"
    exit 0
fi

echo "" >&2
echo "FAIL: AGENTS.md must not cite ADR-NNN — point to domain pages instead:" >&2
echo "$VIOLATIONS" >&2
echo "" >&2
echo "Replace e.g. '(ADR-073)' with '→ docs/architecture/job-model.md' or the owning domain page." >&2
exit 1
