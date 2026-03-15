#!/usr/bin/env bash
# Check that no Python source file exceeds 500 lines (tests excluded).
# Known exceptions are listed below — each must have a tracking issue.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MAX=500
FAIL=0

# Files exempt from the limit (tracked for future refactoring).
EXEMPT=(
    "src/lyra/adapters/telegram.py"    # #196 — adapter protocol inherently large
    "src/lyra/adapters/discord.py"     # #196 — adapter protocol inherently large
    "src/lyra/core/hub.py"             # #196 — central bus, split planned
    "src/lyra/core/agent.py"           # #196 — config loader + runner
    "src/lyra/__main__.py"             # #196 — startup wiring
    "src/lyra/cli_agent.py"            # #268 — all lyra agent CLI commands in one module
)

is_exempt() {
    for e in "${EXEMPT[@]}"; do
        [ "$1" = "$e" ] && return 0
    done
    return 1
}

while IFS= read -r -d '' f; do
    is_exempt "$f" && continue
    LINES=$(wc -l < "$f")
    if [ "$LINES" -gt "$MAX" ]; then
        echo "$f - $LINES lines (max $MAX)"
        FAIL=1
    fi
done < <(find src/ -name "*.py" -print0)

exit $FAIL
