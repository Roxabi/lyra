"""Search session command handler (issue #360).

Runs vault search via the injected SessionTools.vault.
Registered as a session command (not a plugin command) — receives tools via injection.
Stateless — no LLM call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.core.messaging.message import InboundMessage, Response
from lyra.integrations.base import SessionTools

if TYPE_CHECKING:
    from lyra.llm.base import LlmProvider


async def cmd_search(
    msg: InboundMessage,
    driver: "LlmProvider",
    tools: SessionTools,
    args: list[str],
    timeout: float,
) -> Response:
    """Search the vault: /search <query>."""
    if not args:
        return Response(content="Usage: /search <query>")
    query = " ".join(args)
    result = await tools.vault.search(query, timeout=25.0)
    return Response(content=result)
