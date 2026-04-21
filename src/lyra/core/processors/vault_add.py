"""VaultAddProcessor — /vault-add <url> (issue #363).

Pre:  validate URL → scrape → inject content + extraction instructions.
Post: parse LLM title/tags from response → vault.add() side effect.

B3 fix: no mutable instance state — URL is re-extracted from original msg in post().
B8 fix: Response imported at module level (no circular dependency exists).
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING

from lyra.core.messaging.message import Response
from lyra.core.processors._scraping import (
    ScrapingProcessor,
    _extract_and_validate_url,
)
from lyra.core.processors.processor_registry import register
from lyra.integrations.base import VaultWriteFailed

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage

log = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_TAGS_RE = re.compile(r"^Tags:\s*(.+)$", re.MULTILINE)

# Prose-formatted sibling of the JSON schema in
# roxabi-plugins/plugins/web-intel/scripts/enricher.py (ENRICHMENT_PROMPT).
# Same five fields (title, tags, summary, key_points, reply) so Discord replies
# and intel.roxabi.dev modals stay aligned — but streamed as readable markdown
# because Discord sees each chunk as it's generated.
_INSTRUCTION = (
    "Please extract and summarise the web content below for the user.\n\n"
    "Format your response EXACTLY as:\n\n"
    "Title: <real page title, no site suffix>\n"
    "Tags: <3-5 comma-separated short noun phrases>\n\n"
    "<a helpful 2-5 sentence paragraph for the reader; light markdown is fine>\n\n"
    "**Key points**\n"
    "- <short factual bullet under 150 chars>\n"
    "- <short factual bullet under 150 chars>\n"
    "- <optional, up to 5 bullets total>"
)


def _parse_title(text: str, fallback: str) -> str:
    m = _TITLE_RE.search(text)
    return m.group(1).strip() if m else fallback


def _parse_tags(text: str) -> list[str]:
    m = _TAGS_RE.search(text)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


@register(
    "/vault-add",
    description="Save a URL to the vault: /vault-add <url>",
)
class VaultAddProcessor(ScrapingProcessor):
    """Validate URL → scrape → LLM summarise → vault.add() side effect.

    No mutable instance state: URL is re-extracted from the original InboundMessage
    in post(), so this processor is safe for concurrent use even if build() is
    ever cached.
    """

    @property
    def instruction(self) -> str:
        return _INSTRUCTION

    async def post(self, msg: "InboundMessage", response: "Response") -> "Response":
        # B3: re-extract URL from original msg — no self._url needed
        url, err = _extract_and_validate_url(msg)
        if err or not url:
            return response

        title = _parse_title(response.content, url)
        tags = _parse_tags(response.content)
        vault_note = ""
        try:
            await self.tools.vault.add(title, tags, url, response.content, timeout=30.0)
            vault_note = "\n\n✅ Saved to vault."
        except VaultWriteFailed as exc:
            log.warning("VaultAddProcessor: vault write failed (%s)", exc)
            if exc.reason == "not_available":
                vault_note = "\n\n⚠️ Vault CLI not available — summary not saved."
            else:
                vault_note = "\n\n⚠️ Vault write failed — summary not saved."
        except Exception as exc:
            log.warning(
                "VaultAddProcessor: unexpected vault error: %s", exc, exc_info=True
            )
            vault_note = "\n\n⚠️ Vault write failed — summary not saved."

        return dataclasses.replace(response, content=response.content + vault_note)
