"""Search plugin handlers (issue #360).

Runs vault search via the injected VaultProvider.
The provider is set at agent startup via set_vault_provider().
Stateless — no LLM call.
"""
from __future__ import annotations

from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.integrations.base import VaultProvider

_vault_provider: VaultProvider | None = None


def set_vault_provider(vp: VaultProvider | None) -> None:
    """Set (or clear) the vault provider. Called by agents at startup."""
    global _vault_provider
    _vault_provider = vp


async def cmd_search(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Search the vault: /search <query>."""
    if not args:
        return Response(content="Usage: /search <query>")
    query = " ".join(args)
    if _vault_provider is None:
        return Response(content="vault CLI not available.")
    result = await _vault_provider.search(query, timeout=25.0)
    return Response(content=result)
