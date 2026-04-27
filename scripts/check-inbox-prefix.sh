#!/usr/bin/env bash
# Check for raw _INBOX.{name} string construction bypassing identity_name API.
#
# nats_connect(identity_name=...) is the canonical way to set per-identity inbox
# prefix. Raw string construction like inbox_prefix=f"_INBOX.{name}" bypasses
# validation and should be flagged.
#
# Tests are excluded since they may intentionally test the raw parameter.
# Docstrings may trigger warnings but are informational (helps keep docs consistent).
#
# Usage: scripts/check-inbox-prefix.sh
# Returns: 0 if no violations, 1 if violations found
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

FAIL=0

# Find source files (exclude tests)
# Note: tests/ may intentionally test the raw inbox_prefix parameter
find_sources() {
    find src packages -name "*.py" -print0 2>/dev/null | grep -zvE '/tests/'
}

# Check for f-string construction: inbox_prefix=f"_INBOX.
while IFS= read -r -d '' f; do
    matches=$(grep -n 'inbox_prefix=f"_INBOX\.' "$f" 2>/dev/null || true)
    if [ -n "$matches" ]; then
        echo "FAIL: $f uses raw f-string inbox_prefix construction (use identity_name= instead):"
        echo "$matches"
        FAIL=1
    fi
done < <(find_sources)

# Check for literal construction: inbox_prefix="_INBOX.
# Note: This may also match docstring examples (informational, not a hard error for docs)
while IFS= read -r -d '' f; do
    matches=$(grep -n 'inbox_prefix="_INBOX\.' "$f" 2>/dev/null || true)
    if [ -n "$matches" ]; then
        # Check if this is a docstring (contains double backticks around the expression)
        # Docstrings are informational - warn but don't fail
        filtered=$(echo "$matches" | grep -v '``inbox_prefix=' || true)
        if [ -n "$filtered" ]; then
            echo "FAIL: $f uses raw literal inbox_prefix construction (use identity_name= instead):"
            echo "$filtered"
            FAIL=1
        fi
        # Docstring matches are informational only
        docstring_matches=$(echo "$matches" | grep '``inbox_prefix=' || true)
        if [ -n "$docstring_matches" ]; then
            echo "INFO: $f has docstring mentioning inbox_prefix (verify docs recommend identity_name):"
            echo "$docstring_matches"
        fi
    fi
done < <(find_sources)

if [ "$FAIL" -eq 0 ]; then
    echo "OK: no raw _INBOX inbox_prefix constructions found in source files"
fi

exit $FAIL
