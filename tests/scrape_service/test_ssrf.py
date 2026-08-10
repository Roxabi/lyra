"""SSRF policy for scrape service (#2327)."""

from __future__ import annotations

import pytest

from factory.scrape_service.ssrf import SsrfRejected, assert_url_safe, require_https_url


def test_require_https_rejects_http() -> None:
    with pytest.raises(SsrfRejected) as ei:
        require_https_url("http://example.com/")
    assert ei.value.reason == "https_only"


def test_require_https_ok() -> None:
    assert require_https_url("https://example.com/path") == "https://example.com/path"


@pytest.mark.asyncio
async def test_assert_blocks_localhost() -> None:
    with pytest.raises(SsrfRejected) as ei:
        await assert_url_safe("https://127.0.0.1/")
    assert ei.value.reason == "private_ip"


@pytest.mark.asyncio
async def test_assert_blocks_tailscale_cgnat() -> None:
    # 100.64.0.1 is in Tailscale CGNAT — resolve via literal hostname not possible;
    # use IP in host form if URL allows (hostname 100.64.0.1)
    with pytest.raises(SsrfRejected) as ei:
        await assert_url_safe("https://100.64.0.1/")
    assert ei.value.reason == "private_ip"


@pytest.mark.asyncio
async def test_assert_blocks_ipv4_mapped_loopback() -> None:
    with pytest.raises(SsrfRejected) as ei:
        await assert_url_safe("https://[::ffff:127.0.0.1]/")
    assert ei.value.reason == "private_ip"


@pytest.mark.asyncio
async def test_redirect_to_private_rejected() -> None:
    """302 Location to loopback must fail SSRF on the hop (not only first URL)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from factory.scrape_service.extract import fetch_and_extract

    first = MagicMock()
    first.status_code = 302
    first.headers = {"location": "https://127.0.0.1/secret"}
    first.request = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=first)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("factory.scrape_service.extract.httpx.AsyncClient", return_value=client):
        with pytest.raises(SsrfRejected) as ei:
            await fetch_and_extract("https://example.com/")
    assert ei.value.reason == "private_ip"
