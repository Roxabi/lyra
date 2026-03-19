"""SummarizeProcessor — /summarize <url> (issue #363).

Pre:  scrape URL → inject content + bullet-point instruction.
Post: pass-through (response goes straight to history).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from lyra.core.processor_registry import BaseProcessor, register
from lyra.integrations.base import ScrapeFailed

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)

_INSTRUCTION = (
    "Please summarize the following web content in 3-5 bullet points. Be concise."
)


@register(
    "/summarize",
    description="Summarize a URL in bullet points: /summarize <url>",
)
class SummarizeProcessor(BaseProcessor):
    """Scrape URL → inject content → LLM summarizes as bullet points."""

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        url = (
            msg.command.args.strip()
            if msg.command and msg.command.args
            else msg.text.strip()
        )

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
