"""Add-vault session command handler (issue #372).

Saves text content directly to the vault as a note.
Stateless — no LLM call.  Returns a static confirmation.

Replaces the former AddVaultProcessor which unnecessarily routed through the
LLM pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.core.exceptions import VaultWriteFailed
from lyra.core.messaging.message import InboundMessage, Response
from lyra.integrations.base import SessionTools

if TYPE_CHECKING:
    from lyra.llm.base import LlmProvider

log = logging.getLogger(__name__)

_NOTE_CATEGORY = "notes"
_NOTE_TYPE = "note"
_TITLE_MAX_CHARS = 80
_MAX_CONTENT_CHARS = 32_000


async def cmd_add_vault(
    msg: InboundMessage,
    driver: "LlmProvider",
    tools: SessionTools,
    args: list[str],
    timeout: float,
) -> Response:
    """Save a note to the vault: /add-vault <note content>."""
    content = " ".join(args).strip() if args else ""
    if not content:
        return Response(content="Usage: /add-vault <note content>")

    if len(content) > _MAX_CONTENT_CHARS:
        log.warning(
            "cmd_add_vault: content truncated from %d to %d chars",
            len(content),
            _MAX_CONTENT_CHARS,
        )
        content = content[:_MAX_CONTENT_CHARS] + "\n\n[note truncated]"

    title = content[:_TITLE_MAX_CHARS].rstrip()
    try:
        await tools.vault.add(
            title,
            [],
            "",
            content,
            category=_NOTE_CATEGORY,
            entry_type=_NOTE_TYPE,
            timeout=30.0,
        )
    except VaultWriteFailed as exc:
        if exc.reason == "not_available":
            return Response(
                content="Vault CLI not available — note was NOT saved.",
            )
        return Response(
            content=f"Vault write failed ({exc.reason}) — NOT saved.",
        )

    return Response(content="Saved to vault.")
