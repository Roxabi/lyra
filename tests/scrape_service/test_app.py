"""Scrape FastAPI app smoke (#2327)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from factory.scrape_service.app import build_app


@pytest.mark.asyncio
async def test_ready_and_health() -> None:
    app = build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code == 200
        r2 = await client.get("/ready")
        assert r2.status_code == 200
        assert r2.json().get("ready") is True


@pytest.mark.asyncio
async def test_scrape_ssrf_http() -> None:
    app = build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/scrape", json={"url": "http://example.com"})
        assert r.status_code == 400
        assert r.json()["reason"] == "ssrf"


@pytest.mark.asyncio
async def test_scrape_success_mocked() -> None:
    app = build_app()
    transport = ASGITransport(app=app)
    with patch(
        "factory.scrape_service.app.fetch_and_extract",
        new=AsyncMock(return_value="Example Domain text"),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/scrape", json={"url": "https://example.com", "timeout_s": 10}
            )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "Example" in body["text"]
