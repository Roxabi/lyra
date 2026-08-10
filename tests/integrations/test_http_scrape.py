"""HttpScrapeProvider unit tests (#2327)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from factory.core.exceptions import ScrapeFailed
from factory.integrations.http_scrape import HttpScrapeProvider


@pytest.mark.asyncio
async def test_scrape_success() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "text": "hello page", "url": "https://ex.com"}
    client.post = AsyncMock(return_value=resp)

    provider = HttpScrapeProvider(base_url="http://scrape:8455", client=client)
    text = await provider.scrape("https://ex.com")
    assert text == "hello page"
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_timeout() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=httpx.TimeoutException("t"))
    provider = HttpScrapeProvider(base_url="http://scrape:8455", client=client)
    with pytest.raises(ScrapeFailed) as ei:
        await provider.scrape("https://ex.com")
    assert ei.value.reason == "timeout"


@pytest.mark.asyncio
async def test_scrape_unavailable() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    provider = HttpScrapeProvider(base_url="http://scrape:8455", client=client)
    with pytest.raises(ScrapeFailed) as ei:
        await provider.scrape("https://ex.com")
    assert ei.value.reason == "not_available"


@pytest.mark.asyncio
async def test_scrape_ssrf_maps_to_subprocess_error() -> None:
    client = AsyncMock(spec=httpx.AsyncClient)
    resp = MagicMock()
    resp.status_code = 400
    resp.json.return_value = {"success": False, "reason": "ssrf"}
    client.post = AsyncMock(return_value=resp)
    provider = HttpScrapeProvider(base_url="http://scrape:8455", client=client)
    with pytest.raises(ScrapeFailed) as ei:
        await provider.scrape("https://127.0.0.1/")
    assert ei.value.reason == "subprocess_error"


def test_build_scrape_provider_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_SCRAPE_URL", "http://factory-scrape:8455")
    from factory.integrations.http_scrape import build_scrape_provider

    p = build_scrape_provider()
    assert isinstance(p, HttpScrapeProvider)
