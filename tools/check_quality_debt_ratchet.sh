#!/usr/bin/env bash
# Quality-debt ratchet gate.
# Checks three violation classes against a frozen baseline:
#   (a) UNTAGGED rows in src/
#   (b) DEBT counts above baseline
#   (c) Stale registry references in src/
#
# Env vars:
#   QG_AUDIT_REPORT   path to audit report JSON (skips re-running audit tool)
#   QG_BASELINE       path to baseline JSON
#   RATCHET_MODE      "soft" | "hard" (overrides cutover-date logic)
#   QG_TODAY          ISO date YYYY-MM-DD for testability (default: system date)
set -euo pipefail

# ---------------------------------------------------------------------------
# Config / defaults
# ---------------------------------------------------------------------------
# Derive repo root from script location (script lives in <repo>/tools/).
# This avoids relying on git rev-parse so the script works when cwd is not
# a git repo (e.g. pytest tmp_path).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
BASELINE="${QG_BASELINE:-${REPO_ROOT}/tools/quality_debt_baseline.json}"
AUDIT_REPORT="${QG_AUDIT_REPORT:-${REPO_ROOT}/artifacts/quality-debt-report.json}"

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! command -v jq &>/dev/null; then
    echo "error: jq is required but not installed. Install jq to use this gate." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Load baseline
# ---------------------------------------------------------------------------
if [[ ! -f "$BASELINE" ]]; then
    echo "baseline missing: $BASELINE — run 'make quality-debt-rebaseline' to create it." >&2
    exit 1
fi

generated_by="$(jq -r '.generated_by // empty' "$BASELINE")"
if [[ "$generated_by" != "make quality-debt-rebaseline" ]]; then
    echo "baseline generated_by mismatch ('${generated_by}') — regenerate via 'make quality-debt-rebaseline'" >&2
    exit 1
fi

cutover_date="$(jq -r '.cutover_date // empty' "$BASELINE")"

# ---------------------------------------------------------------------------
# Load audit report (or run audit tool)
# ---------------------------------------------------------------------------
if [[ -f "$AUDIT_REPORT" ]]; then
    report="$AUDIT_REPORT"
else
    tmp_report="$(mktemp /tmp/quality-debt-report.XXXXXX.json)"
    trap 'rm -f "$tmp_report"' EXIT
    uv run python "${REPO_ROOT}/tools/audit_quality_debt.py" \
        --root "${REPO_ROOT}" \
        --out "$tmp_report" >/dev/null
    report="$tmp_report"
fi

# ---------------------------------------------------------------------------
# Determine mode
# ---------------------------------------------------------------------------
today="${QG_TODAY:-$(date +%F)}"

if [[ -n "${RATCHET_MODE:-}" ]]; then
    mode="$RATCHET_MODE"
elif [[ "$today" > "$cutover_date" || "$today" == "$cutover_date" ]]; then
    mode="hard"
else
    mode="soft"
fi

case "$mode" in
    soft|hard) ;;
    "")
        echo "ratchet: internal error — mode not determined" >&2
        exit 1
        ;;
    *)
        echo "ratchet: invalid mode '$mode' (expected 'soft' or 'hard')" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Violation detection
# ---------------------------------------------------------------------------
violations=0

# (a) UNTAGGED rows in src/
untagged_count="$(jq '[.rows[] | select(.bucket == "UNTAGGED" and (.path | startswith("src/")))] | length' "$report")"
if [[ "$untagged_count" -gt 0 ]]; then
    echo "WARN: ${untagged_count} untagged suppression(s) found in src/ — add POLICY:<tag> or DEBT:<slug> suffix." >&2
    violations=1
fi

# (b) DEBT counts above baseline
# Iterate over all (rule, DEBT, slug) triples where baseline count is set.
# Also flag any new triples that appear in report but not in baseline.

# Pre-validate report structure before iterating.
if ! jq -e '.counts_by_rule_bucket_slug | type == "object"' "$report" >/dev/null 2>&1; then
    echo "ratchet: report missing or malformed counts_by_rule_bucket_slug — regenerate via 'make quality-debt-report'" >&2
    exit 1
fi

jq_log="$(mktemp)"
trap 'rm -f "$jq_log"' EXIT
while IFS=$'\t' read -r rule slug report_count; do
    baseline_count="$(jq -r --arg rule "$rule" --arg slug "$slug" \
        '.counts[$rule].DEBT[$slug] // 0' "$BASELINE")"
    if [[ "$report_count" -gt "$baseline_count" ]]; then
        echo "WARN: count above baseline for rule=${rule} slug=${slug}: ${report_count} > ${baseline_count}" >&2
        violations=1
    fi
done < <(jq -r '
    .counts_by_rule_bucket_slug
    | to_entries[]
    | .key as $rule
    | .value.DEBT? // {}
    | to_entries[]
    | [$rule, .key, (.value | tostring)]
    | @tsv
' "$report" 2>"$jq_log") || {
    echo "ratchet: jq failed parsing $report:" >&2
    cat "$jq_log" >&2
    exit 1
}

# (c) Stale registry references in src/
stale_src_count="$(jq '[.stale_references[] | select(.path | startswith("src/"))] | length' "$report")"
if [[ "$stale_src_count" -gt 0 ]]; then
    echo "WARN: ${stale_src_count} stale registry reference(s) in src/ — update or remove debt registry entries." >&2
    violations=1
fi

# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------
if [[ "$violations" -gt 0 && "$mode" == "hard" ]]; then
    exit 1
fi

exit 0
