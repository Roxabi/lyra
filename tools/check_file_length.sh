#!/usr/bin/env bash
# Check that no Python source file exceeds 300 lines (tests excluded).
# Known exceptions are listed below — each must have a tracking issue.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MAX=300
FAIL=0

# Files exempt from the limit (tracked for future refactoring).
EXEMPT=(
    "src/lyra/core/hub/hub.py"              # 791 lines — #396 refactor backlog
    "src/lyra/bootstrap/hub_standalone.py"  # 432 lines — #396 refactor backlog
    "src/lyra/adapters/_shared.py"          # 432 lines — #396 refactor backlog
    "src/lyra/core/cli_pool.py"             # 430 lines — #396 refactor backlog
    "src/lyra/core/stores/agent_store.py"   # 341 lines — #396 refactor backlog
    "src/lyra/adapters/discord.py"          # 322 lines — #196 adapter protocol
    "src/lyra/core/commands/command_router.py" # 310 lines — #396 refactor backlog
    "src/lyra/core/cli_streaming.py"        # 304 lines — #753 new extraction (4 lines over, needs trim)
    "src/lyra/core/stores/turn_store.py"    # 301 lines — #753 1 line over, will migrate to infrastructure in PR 11
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
