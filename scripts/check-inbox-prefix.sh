#!/usr/bin/env bash
# Check for raw _INBOX.{name} string construction bypassing identity_name API.
#
# nats_connect(identity_name=...) is the canonical way to set per-identity inbox
# prefix. Raw string construction like inbox_prefix=f"_INBOX.{name}" bypasses
# validation and should be flagged.
#
# Tests are excluded since they may intentionally test the raw parameter.
#
# Usage: scripts/check-inbox-prefix.sh
# Returns: 0 if no violations, 1 if violations found
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

FAIL=0

# Find source files (exclude tests)
# Note: tests/ may intentionally test the raw inbox_prefix parameter
find_sources() {
    find src packages -name "*.py" -not -path "*/tests/*" -print0 2>/dev/null
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
# Uses Python AST to avoid false positives from comments/docstrings
while IFS= read -r -d '' f; do
    result=$(python3 - "$f" <<'PYEOF'
import ast, sys
try:
    tree = ast.parse(open(sys.argv[1]).read())
except SyntaxError:
    sys.exit(0)
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        for kw in node.keywords:
            if (kw.arg == 'inbox_prefix' and
                    isinstance(kw.value, ast.Constant) and
                    isinstance(kw.value.value, str) and
                    kw.value.value.startswith('_INBOX.')):
                print(f"{sys.argv[1]}:{kw.value.lineno}: inbox_prefix=\"{kw.value.value}...\"")
PYEOF
    )
    if [ -n "$result" ]; then
        echo "FAIL: uses raw literal inbox_prefix construction (use identity_name= instead):"
        echo "$result"
        FAIL=1
    fi
done < <(find_sources)

if [ "$FAIL" -eq 0 ]; then
    echo "OK: no raw _INBOX inbox_prefix constructions found in source files"
fi

exit $FAIL
