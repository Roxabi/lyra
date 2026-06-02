#!/usr/bin/env bash
# check_test_sleep.sh — block raw sleep() calls in tests without a sync comment.
#
# A sleep() call is flagged when ALL of the following are true:
#   1. The line contains asyncio.sleep( or time.sleep( (an actual invocation).
#   2. The same line does NOT contain "# event-based" or "# NATS delivery window".
#   3. The line is not a mock/patch assignment (does not contain "patch",
#      "mock", "Mock", "_RL_SLEEP", or "asyncio.sleep = ").
#
# Rationale: timing-based sleeps in tests are flaky under CI load. Every sleep
# must document why it cannot be replaced with event-based synchronisation:
#   - "# event-based" — a genuine event loop yield, cancellation park, or
#     filesystem-timing wait that is structurally unavoidable.
#   - "# NATS delivery window" — waiting for NATS message propagation where
#     an event-based alternative would require infrastructure changes.
#
# Exit-code contract (consistent with gate scripts in this repo — tools/CLAUDE.md):
#   0 = no violations found
#   1 = violations detected (merge-blocking)
#   2 = script error (missing dep / corrupt git state)
#
# Environment overrides:
#   SLEEP_SCAN_ROOT  — directory to scan (default: tests/)
#
# Run locally:
#   bash tools/check_test_sleep.sh
# Run in CI:
#   check-test-sleep step in .github/workflows/ci.yml
set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root and move there
# ---------------------------------------------------------------------------
cd "$(git rev-parse --show-toplevel)" || { echo "ERROR: not a git repository" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCAN_ROOT="${SLEEP_SCAN_ROOT:-tests/}"

# ---------------------------------------------------------------------------
# Guard: scan root must exist
# ---------------------------------------------------------------------------
if [ ! -d "$SCAN_ROOT" ]; then
    echo "WARN: $SCAN_ROOT not found, skipping check_test_sleep" >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Scan: grep for actual sleep invocations, filter out non-invocations
# ---------------------------------------------------------------------------
FAIL=0
VIOLATION_COUNT=0

while IFS= read -r match; do
    # Extract file and line content
    file="${match%%:*}"
    rest="${match#*:}"
    lineno="${rest%%:*}"
    content="${rest#*:}"

    # Skip comment-only lines and docstring lines — not real invocations
    trimmed="${content#"${content%%[![:space:]]*}"}"  # lstrip
    case "$trimmed" in
        "#"*|'"""'*|"'''"*) continue ;;
    esac

    # Skip mock/patch lines — these are not real sleeps
    if echo "$content" | grep -qE 'patch|[Mm]ock|_RL_SLEEP|asyncio\.sleep\s*='; then
        continue
    fi

    # Skip lines that already have the required comment
    if echo "$content" | grep -qF '# event-based'; then
        continue
    fi
    if echo "$content" | grep -qF '# NATS delivery window'; then
        continue
    fi

    # Violation
    if [ "$VIOLATION_COUNT" -eq 0 ]; then
        echo "" >&2
        echo "FAIL: raw sleep() calls in tests without sync comment:" >&2
    fi
    echo "  ${file}:${lineno}: ${content}" >&2
    echo "::error file=${file},line=${lineno}::raw sleep() — add '# event-based' or '# NATS delivery window' comment"
    VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
    FAIL=1
done < <(
    grep -rn "asyncio\.sleep(\|time\.sleep(" "$SCAN_ROOT" \
        --include="*.py" \
        2>/dev/null \
    || true
)

if [ "$FAIL" -eq 0 ]; then
    echo "check_test_sleep: no raw sleep() calls found in ${SCAN_ROOT} — OK"
else
    echo "" >&2
    echo "Found ${VIOLATION_COUNT} raw sleep() call(s) without sync comment." >&2
    echo "Remediation: add one of the following inline comments on the same line:" >&2
    echo "  # event-based   — event loop yield, cancellation park, or filesystem timing" >&2
    echo "  # NATS delivery window   — waiting for NATS message propagation" >&2
fi

exit $FAIL
