"""Shared base for URL-scraping processors (issue #363).

ExplainProcessor and SummarizeProcessor both scrape a URL and inject content
into the message. This ABC extracts the shared logic so it lives in one place.
"""

from __future__ import annotations

import dataclasses
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING

from lyra.core.processor_registry import BaseProcessor
from lyra.integrations.base import ScrapeFailed

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)

_SAFE_SCRAPE_MAX_CHARS = 32_000  # B5: prompt-injection + DoS guard


def _extract_and_validate_url(msg: "InboundMessage") -> tuple[str, str | None]:
    """Return (url, error_text_or_None).

    B4+B6: validates that a URL is present and uses http/https scheme.
    Returns (url, None) on success, ("", error_message) on failure.
    """
    url = (
        msg.command.args.strip()
        if msg.command and msg.command.args
        else msg.text.strip()
    )
    if not url or not url.startswith(("http://", "https://")):
        cmd = f"/{msg.command.name}" if msg.command else "this command"
        return "", f"Usage: {cmd} <url>  (must start with http:// or https://)"
    return url, None


async def _scrape_with_fallback(scraper, url: str, timeout: float) -> str:
    """Scrape *url* via scraper; return fallback string on any ScrapeFailed.

    B7: single shared implementation — no more copy-paste across 3 files.
    """
    try:
        return await scraper.scrape(url, timeout=timeout)
    except ScrapeFailed as exc:
        if exc.reason == "not_available":
            return f"[scraping unavailable] {url}"
        if exc.reason == "timeout":
            return f"[scrape timed out] {url}"
        return f"[scrape failed] {url}"


class ScrapingProcessor(BaseProcessor):
    """ABC for processors that scrape a URL and inject content.

    Subclasses must implement `instruction` (the prompt prefix injected before
    the scraped content).  `pre()` handles URL validation, scraping, truncation,
    and message enrichment — subclasses get all of this for free.
    """

    @property
    @abstractmethod
    def instruction(self) -> str:
        """The instruction injected before the scraped content."""
        ...

    async def pre(self, msg: "InboundMessage") -> "InboundMessage":
        url, err = _extract_and_validate_url(msg)
        if err:
            return dataclasses.replace(msg, text=err)

        scraped = await _scrape_with_fallback(self.tools.scraper, url, timeout=30.0)

        # B5: truncate to guard against prompt injection and token DoS
        if len(scraped) > _SAFE_SCRAPE_MAX_CHARS:
            log.warning(
                "ScrapingProcessor: scraped content truncated"
                " from %d to %d chars for %s",
                len(scraped),
                _SAFE_SCRAPE_MAX_CHARS,
                url,
            )
            scraped = scraped[:_SAFE_SCRAPE_MAX_CHARS] + "\n\n[content truncated]"

        enriched = f"{self.instruction}\n\nURL: {url}\n\n{scraped}"
        return dataclasses.replace(msg, text=enriched)
