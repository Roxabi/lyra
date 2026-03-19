"""VaultAddProcessor — /vault-add <url> (issue #363).

Pre:  scrape URL → inject content + extraction instructions into message.
Post: parse LLM title/tags from response → vault.add() side effect.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING

from lyra.core.processor_registry import BaseProcessor, register
from lyra.integrations.base import ScrapeFailed, VaultWriteFailed

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage, Response

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
    """Scrape → LLM summarise → vault.add() side effect."""

    def __init__(self, tools) -> None:
        super().__init__(tools)
        self._url: str = ""

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        url = (
            msg.command.args.strip()
            if msg.command and msg.command.args
            else msg.text.strip()
        )
        self._url = url

        try:
            scraped = await self.tools.scraper.scrape(url, timeout=30.0)
        except ScrapeFailed as exc:
            if exc.reason == "not_available":
                scraped = f"[scraping unavailable] {url}"
            elif exc.reason == "timeout":
                scraped = f"[scrape timed out] {url}"
            else:
                scraped = f"[scrape failed] {url}"

        enriched = f"{_INSTRUCTION}\n\nURL: {url}\n\n{scraped}"
        return dataclasses.replace(msg, text=enriched)

    async def post(
        self, msg: "InboundMessage", response: "Response"
    ) -> "Response":
        if not self._url:
            return response

        title = _parse_title(response.content, self._url)
        tags = _parse_tags(response.content)
        vault_note = ""
        try:
            await self.tools.vault.add(
                title, tags, self._url, response.content, timeout=30.0
            )
        except VaultWriteFailed as exc:
            log.warning("VaultAddProcessor: vault write failed (%s)", exc)
            if exc.reason == "not_available":
                vault_note = "\n\n(vault CLI not available — summary not saved)"
            else:
                vault_note = "\n\n(vault write failed — summary not saved)"

        if vault_note:
            from lyra.core.message import Response as Resp

            return Resp(
                content=response.content + vault_note,
                metadata=response.metadata,
                speak=response.speak,
            )
        return response
