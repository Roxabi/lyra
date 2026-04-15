#!/usr/bin/env bash
# Check that no Python source file exceeds 300 lines (tests excluded).
# Known exceptions are listed below — each must have a tracking issue.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MAX=300
FAIL=0

# Files exempt from the limit (tracked for future refactoring).
EXEMPT=(
    "src/lyra/core/cli_protocol.py"         # 494 lines — #396 refactor backlog
    "src/lyra/core/hub/middleware_submit.py" # 327 lines — path extraction refactor (#626)
    "src/lyra/core/stores/agent_store.py"   # 449 lines — #396 refactor backlog
    "src/lyra/cli_agent_crud.py"            # 441 lines — #396 refactor backlog
    "src/lyra/core/pool/pool_processor.py"  # 406 lines — #396 refactor backlog
    "src/lyra/core/hub/outbound_dispatcher.py" # 377 lines — #396 refactor backlog
    "src/lyra/core/commands/command_router.py" # 374 lines — #396 refactor backlog
    "src/lyra/core/agent_refiner.py"        # 367 lines — #396 refactor backlog
    "src/lyra/core/hub/hub_outbound.py"     # 356 lines — #396 refactor backlog
    "src/lyra/adapters/telegram_outbound.py" # 356 lines — #396 refactor backlog
    "src/lyra/core/hub/hub.py"              # 353 lines — #396 refactor backlog
    "src/lyra/adapters/discord_outbound.py" # 353 lines — #396 refactor backlog
    "src/lyra/tts/__init__.py"              # 336 lines — #396 refactor backlog
    "src/lyra/adapters/discord.py"          # 311 lines — #196 adapter protocol
    "src/lyra/adapters/_shared.py"          # 346 lines — #396 refactor backlog
    "src/lyra/core/audio_pipeline.py"       # 313 lines — #396 refactor backlog
    "src/lyra/core/cli_pool.py"             # 401 lines — #396 refactor backlog
    "src/lyra/agents/simple_agent.py"       # 306 lines — #396 refactor backlog
    "src/lyra/bootstrap/hub_standalone.py"  # 446 lines — #396 refactor backlog
    "src/lyra/bootstrap/unified.py"         # 304 lines — #396 refactor backlog
    "src/lyra/core/stores/turn_store.py"    # 325 lines — #396 refactor backlog
    "src/lyra/adapters/nats_outbound_listener.py" # 317 lines — #396 refactor backlog
    "src/lyra/bootstrap/adapter_standalone.py"    # 310 lines — #721 import rewrites + ruff
    "src/lyra/core/hub/middleware_stages.py"      # 307 lines — #721 import rewrites + ruff
    "src/lyra/adapters/_shared_streaming.py"      # 302 lines — #721 import rewrites + ruff
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
