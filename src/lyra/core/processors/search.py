"""SearchProcessor — /search <query> (issue #363).

Pre:  run vault search → inject results into message so LLM can present them.
Post: pass-through (formatted results go straight to history, enabling follow-ups).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from lyra.core.processor_registry import BaseProcessor, register

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)


@register(
    "/search",
    description="Search the vault: /search <query>",
)
class SearchProcessor(BaseProcessor):
    """Run vault search → inject results → LLM formats and contextualises."""

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        query = (
            msg.command.args.strip()
            if msg.command and msg.command.args
            else msg.text.strip()
        )

        try:
            results = await self.tools.vault.search(query, timeout=25.0)
        except Exception:
            log.warning("SearchProcessor: vault.search() failed", exc_info=True)
            results = "(search unavailable)"

        if not results or not results.strip():
            results = f'(no results found for "{query}")'

        enriched = (
            f'Search results for "{query}":\n\n'
            f"{results}\n\n"
            "Please present these results in a helpful, readable format. "
            "If there are no relevant results, say so clearly."
        )
        return dataclasses.replace(msg, text=enriched)
