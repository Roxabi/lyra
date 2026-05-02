#!/usr/bin/env bash
# check_worker_error_soak_gate.sh — WorkerError soak gate
#
# Reads journald (or a fixture file) and checks that:
#   - At least one METRIC worker_error_populated_total domain=cli line exists
#   - At least one METRIC worker_error_populated_total domain=llm line exists
#   - Zero legacy error_text=<non-empty> lines exist
#
# Usage:
#   tools/check_worker_error_soak_gate.sh [--since "<spec>"] [--fixture PATH] [--help]
#
# Exit:  0 = PASS, 1 = FAIL
# Stdout: "PASS" or "FAIL"
# Stderr: per-counter breakdown

set -euo pipefail

FIXTURE=""
SINCE="48 hours ago"

usage() {
    cat >&2 <<'EOF'
Usage: tools/check_worker_error_soak_gate.sh [OPTIONS]

Options:
  --fixture PATH     Read input from file at PATH instead of journalctl.
  --since SPEC       Override journalctl time window (default: "48 hours ago").
                     Examples: "1 hour ago", "2026-05-01 00:00:00"
  --help             Print this usage message and exit 0.

Gate condition (PASS when all are true):
  - worker_error_populated_total domain=cli  >= 1
  - worker_error_populated_total domain=llm  >= 1
  - legacy error_text=<non-empty>           == 0

Output:
  stdout: PASS or FAIL
  stderr: per-counter breakdown line
EOF
    exit 0
}

# Parse args
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h)
            usage
            ;;
        --fixture)
            shift
            FIXTURE="${1:-}"
            if [ -z "$FIXTURE" ]; then
                echo "ERROR: --fixture requires a PATH argument" >&2
                exit 1
            fi
            ;;
        --since)
            shift
            SINCE="${1:-}"
            if [ -z "$SINCE" ]; then
                echo "ERROR: --since requires a time spec argument" >&2
                exit 1
            fi
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
    shift
done

# Acquire input stream
if [ -n "$FIXTURE" ]; then
    if [ ! -f "$FIXTURE" ]; then
        echo "ERROR: Fixture file not found: $FIXTURE" >&2
        exit 1
    fi
    INPUT_CMD="cat -- $FIXTURE"
else
    INPUT_CMD="journalctl --since \"$SINCE\" --no-pager"
fi

# Count populated_total domain=cli
pop_cli=$(eval "$INPUT_CMD" | grep -c 'METRIC worker_error_populated_total domain=cli' || true)

# Count populated_total domain=llm
pop_llm=$(eval "$INPUT_CMD" | grep -c 'METRIC worker_error_populated_total domain=llm' || true)

# Count any populated_total
pop_total=$(eval "$INPUT_CMD" | grep -c 'METRIC worker_error_populated_total' || true)

# Count legacy error_text= with a non-empty, non-None, non-"" value.
# Uses awk for precise field extraction (POSIX portable — no lookahead needed).
# Algorithm:
#   1. Match lines containing "error_text=".
#   2. Extract the value after "error_text=" (everything up to next space/tab or EOL).
#   3. Exclude: empty string (""), None, and bare empty (nothing after =).
# This correctly handles: error_text="" → skip, error_text=None → skip,
#                         error_text= → skip, error_text="msg" → count,
#                         error_text=raw → count.
legacy_text=$(eval "$INPUT_CMD" | awk '
    /error_text=/ {
        # Extract value after error_text=
        n = split($0, parts, "error_text=")
        if (n < 2) next
        # Take everything after the first "error_text=" up to next whitespace
        val = parts[2]
        sub(/[ \t].*/, "", val)
        # Skip empty, None, ""
        if (val == "" || val == "None" || val == "\"\"") next
        count++
    }
    END { print count+0 }
' || true)

# Breakdown to stderr
printf 'populated_cli=%s populated_llm=%s populated_total=%s legacy_text=%s\n' \
    "$pop_cli" "$pop_llm" "$pop_total" "$legacy_text" >&2

# Gate evaluation
if [ "$pop_cli" -gt 0 ] && [ "$pop_llm" -gt 0 ] && [ "$legacy_text" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
