#!/usr/bin/env bash
# check_str_exc_bus_bound.sh — block str(exc) / f"{exc}" from flowing into
# SanitizedError construction or NATS-bus-bound message fields.
# Escape hatch: add "# str-exc-ok: <reason>" on the offending line.
# EXIT CODES: 0=clean, 1=violations, 2=script error

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || { echo "ERROR: not a git repository" >&2; exit 2; }
cd "$REPO_ROOT"

SCAN_ROOTS="${STR_EXC_SCAN_ROOTS:-src/factory/transport/ src/factory/outbound/ src/factory/core/processors/ src/factory/core/cli/ src/factory/streaming/}"

# Build find args for existing dirs only
FIND_DIRS=()
for d in $SCAN_ROOTS; do
    [ -d "$d" ] && FIND_DIRS+=("$d")
done
if [ ${#FIND_DIRS[@]} -eq 0 ]; then
    echo "check_str_exc_bus_bound: no scan roots found — OK" ; exit 0
fi

VIOLATIONS="$(
    find "${FIND_DIRS[@]}" -type f -name "*.py" -print0 \
    | sort -z \
    | xargs -0 grep -En \
        'message=str\(exc|message=f"[^"]*\{exc|message=repr\(exc|message=format\(exc|from_message\(str\(exc|from_message\(f"[^"]*\{exc' \
        2>/dev/null || true \
    | grep -v '# str-exc-ok:' \
    | grep -v '^[^:]*:[0-9]*:[[:space:]]*#' \
    || true
)"

if [ -z "$VIOLATIONS" ]; then
    echo "check_str_exc_bus_bound: no bus-bound str(exc) patterns found — OK"
    exit 0
fi

echo "" >&2
echo "FAIL: bus-bound str(exc) patterns found (SanitizedError discipline #1212):" >&2
while IFS= read -r v; do
    [ -n "$v" ] || continue
    filepath="${v%%:*}"
    echo "  $v" >&2
    echo "::error file=${filepath}::bus-bound str(exc) — use type(exc).__name__ or SanitizedError.from_message() with pre-scrubbed text; add '# str-exc-ok: <reason>' if intentional"
done <<< "$VIOLATIONS"
cnt="$(printf '%s\n' "$VIOLATIONS" | grep -c .)"
echo "" >&2
echo "Found ${cnt} bus-bound str(exc) violation(s)." >&2
echo "Remediation: use type(exc).__name__ (never leaks internals) or SanitizedError.from_message() for pre-scrubbed upstream text." >&2
exit 1
