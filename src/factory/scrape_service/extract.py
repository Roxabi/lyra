"""Fetch URL with redirect revalidation and extract plain text.

Transitional (#2327): prod Quadlet pins ghcr.io/roxabi/intel-scrape (#2338).
Do not grow this into a second long-term engine.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from factory.scrape_service.ssrf import SsrfRejected, assert_url_safe

log = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_MAX_BYTES = 2_000_000  # const-ok: response body cap before extract
_MAX_TEXT = 32_000  # align with hub ScrapingProcessor


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: scrape-html — bad HTML must not crash service
        # Fallback: crude tag strip
        return re.sub(r"<[^>]+>", " ", html)
    return parser.text()


async def fetch_and_extract(url: str, *, timeout: float = 30.0) -> str:
    """GET *url* (HTTPS), revalidate each redirect, return extracted text.

    Raises:
        SsrfRejected — policy violation
        httpx.HTTPError — network/HTTP failure
        ValueError — empty body / no text
    """
    current = await assert_url_safe(url)
    timeout_cfg = httpx.Timeout(timeout)
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout_cfg,
        headers={"User-Agent": "factory-scrape/1.0 (+https://roxabi.dev)"},
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            resp = await client.get(current)
            if resp.status_code in {301, 302, 303, 307, 308}:
                loc = resp.headers.get("location")
                if not loc:
                    raise ValueError("redirect_without_location")
                next_url = urljoin(current, loc)
                # Absolute http:// redirect must fail https_only
                current = await assert_url_safe(next_url)
                continue
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            raw = resp.content[:_MAX_BYTES]
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" in ctype or raw.lstrip()[:1] == b"<":
                text = html_to_text(raw.decode("utf-8", errors="replace"))
            else:
                text = raw.decode("utf-8", errors="replace").strip()
            text = text[:_MAX_TEXT]
            if not text:
                raise ValueError("empty_extract")
            return text
    raise SsrfRejected("too_many_redirects")
