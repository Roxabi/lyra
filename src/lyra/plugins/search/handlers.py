"""Search plugin handlers (issue #99).

Runs vault search <query> as a subprocess and returns formatted results.
Stateless — no LLM call.
"""

from __future__ import annotations

import lyra.core.session_helpers as _helpers
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool


async def cmd_search(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Search the vault: /search <query>."""
    if not args:
        return Response(content="Usage: /search <query>")

    query = " ".join(args)
    result = await _helpers.vault_search(query, timeout=25.0)
    return Response(content=result)
