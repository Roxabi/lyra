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

from lyra.core.message import Response
from lyra.core.processor_registry import BaseProcessor, register
from lyra.core.processors._scraping import (
    _SAFE_SCRAPE_MAX_CHARS,
    _extract_and_validate_url,
    _scrape_with_fallback,
)
from lyra.integrations.base import VaultWriteFailed

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"^Title:\s*(.+)$", re.MULTILINE)
_TAGS_RE = re.compile(r"^Tags:\s*(.+)$", re.MULTILINE)

_INSTRUCTION = (
    "Please extract the following from the web content below and then provide "
    "a helpful summary for the user.\n\n"
    "Format the FIRST paragraph of your response as:\n"
    "Title: <title>\n"
    "Tags: <3-5 comma-separated tags>\n\n"
    "Then write a clear, concise summary."
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
class VaultAddProcessor(BaseProcessor):
    """Validate URL → scrape → LLM summarise → vault.add() side effect.

    No mutable instance state: URL is re-extracted from the original InboundMessage
    in post(), so this processor is safe for concurrent use even if build() is
    ever cached.
    """

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        url, err = _extract_and_validate_url(msg)
        if err:
            return dataclasses.replace(msg, text=err)

        scraped = await _scrape_with_fallback(self.tools.scraper, url, timeout=30.0)

        # B5: truncate to guard against prompt injection and token DoS
        if len(scraped) > _SAFE_SCRAPE_MAX_CHARS:
            log.warning(
                "VaultAddProcessor: scraped content truncated"
                " from %d to %d chars for %s",
                len(scraped),
                _SAFE_SCRAPE_MAX_CHARS,
                url,
            )
            scraped = scraped[:_SAFE_SCRAPE_MAX_CHARS] + "\n\n[content truncated]"

        enriched = f"{_INSTRUCTION}\n\nURL: {url}\n\n{scraped}"
        return dataclasses.replace(msg, text=enriched)

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
        except VaultWriteFailed as exc:
            log.warning("VaultAddProcessor: vault write failed (%s)", exc)
            if exc.reason == "not_available":
                vault_note = "\n\n(vault CLI not available — summary not saved)"
            else:
                vault_note = "\n\n(vault write failed — summary not saved)"

        if vault_note:
            return dataclasses.replace(response, content=response.content + vault_note)
        return response
