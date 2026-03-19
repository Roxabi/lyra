"""Shared base for URL-scraping processors (issue #363).

ExplainProcessor and SummarizeProcessor both scrape a URL and inject content
into the message. This ABC extracts the shared logic so it lives in one place.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import logging
import socket
from abc import abstractmethod
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lyra.core.processor_registry import BaseProcessor
from lyra.integrations.base import ScrapeFailed

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)

_SAFE_SCRAPE_MAX_CHARS = 32_000  # B5: prompt-injection + DoS guard


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to any private/reserved IP address.

    Checks against RFC-1918, loopback, link-local, multicast, and other
    reserved ranges to prevent SSRF attacks targeting internal services.

    May perform a DNS lookup; callers should treat this as a potentially
    blocking operation.
    """
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Unresolvable hostname — treat as safe to pass through; the scraper
        # will fail on its own with a connection error.
        return False

    for _family, _type, _proto, _canonname, sockaddr in results:
        addr_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            continue

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True

    return False


def _extract_and_validate_url(msg: "InboundMessage") -> tuple[str, str | None]:
    """Return (url, error_text_or_None).

    B4+B6: validates that a URL is present and uses http/https scheme.
    B8 (SSRF): rejects URLs whose hostname resolves to private/LAN addresses.
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

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if _is_private_ip(hostname):
        cmd = f"/{msg.command.name}" if msg.command else "cmd"
        return "", f"Usage: /{cmd} <url> (no private/LAN IP addresses allowed)"

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
