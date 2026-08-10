"""HttpScrapeProvider — ScrapeProvider over FACTORY_SCRAPE_URL (#2327)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from factory.core.exceptions import ScrapeFailed

log = logging.getLogger(__name__)


def _scrape_base_url() -> str | None:
    raw = os.environ.get("FACTORY_SCRAPE_URL", "").strip()
    return raw.rstrip("/") if raw else None


def _map_failure_body(data: dict[str, Any], status_code: int) -> ScrapeFailed:
    reason = data.get("reason", "")
    if reason == "timeout" or status_code == 504:
        return ScrapeFailed("timeout")
    if reason == "unavailable" or status_code == 503:
        return ScrapeFailed("not_available")
    if status_code >= 500:
        return ScrapeFailed("not_available")
    return ScrapeFailed("subprocess_error")


def _parse_json_dict(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class HttpScrapeProvider:
    """Async scraper backed by the factory-scrape HTTP service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        token: str | None = None,
    ) -> None:
        resolved = base_url if base_url is not None else _scrape_base_url()
        if not resolved:
            raise ValueError("FACTORY_SCRAPE_URL is not set")
        self._base = resolved.rstrip("/")
        self._client = client
        tok = token if token is not None else os.environ.get("FACTORY_SCRAPE_TOKEN", "")
        self._token = tok.strip()

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def scrape(self, url: str, timeout: float = 30.0) -> str:
        """POST /scrape; map transport/API failures to ScrapeFailed."""
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout + 5.0)
        )
        try:
            try:
                resp = await client.post(
                    f"{self._base}/scrape",
                    json={"url": url, "timeout_s": timeout},
                    headers=self._auth_headers(),
                )
            except httpx.TimeoutException as exc:
                raise ScrapeFailed("timeout") from exc
            except httpx.RequestError as exc:
                log.warning(
                    "HttpScrapeProvider: request error type=%s",
                    type(exc).__name__,
                )
                raise ScrapeFailed("not_available") from exc

            data = _parse_json_dict(resp)
            if resp.status_code >= 400 or not data.get("success"):
                raise _map_failure_body(data, resp.status_code)
            text = data.get("text") or ""
            if not text:
                raise ScrapeFailed("subprocess_error")
            return str(text)
        finally:
            if own_client:
                await client.aclose()


def build_scrape_provider() -> Any:
    """Return HttpScrapeProvider if FACTORY_SCRAPE_URL set, else WebIntelScraper.

    Container deploys (CONTAINER_NAME set) **require** FACTORY_SCRAPE_URL —
    fail closed rather than falling back to host WebIntel (missing in images).
    """
    if _scrape_base_url():
        return HttpScrapeProvider()
    if os.environ.get("CONTAINER_NAME", "").strip():
        raise RuntimeError(
            "FACTORY_SCRAPE_URL is required in container deploys "
            "(CONTAINER_NAME is set). Point hub at factory-scrape HTTP service."
        )
    from factory.integrations.web_intel import WebIntelScraper

    return WebIntelScraper()
