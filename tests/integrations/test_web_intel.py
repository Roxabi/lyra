"""Tests for WebIntelScraper (lyra.integrations.web_intel)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.integrations.base import ScrapeFailed, ScrapeProvider
from lyra.integrations.web_intel import WebIntelScraper


def _make_proc(returncode=0, stdout=b'', stderr=b''):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc

class TestWebIntelScraper:
    def test_implements_scrape_provider(self):
        assert isinstance(WebIntelScraper(), ScrapeProvider)

    @pytest.mark.asyncio
    async def test_happy_path_returns_text(self):
        payload = b'{"success": true, "data": {"text": "scraped content"}}'
        proc = _make_proc(stdout=payload)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await WebIntelScraper().scrape("https://example.com")
        assert result == "scraped content"

    @pytest.mark.asyncio
    async def test_url_is_last_positional_arg(self):
        payload = b'{"success": true, "data": {"text": "ok"}}'
        proc = _make_proc(stdout=payload)
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return proc
        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await WebIntelScraper().scrape("https://example.com/page")
        assert calls[0][0] == "uv"
        assert calls[0][-1] == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_file_not_found_raises_not_available(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(ScrapeFailed) as exc:
                await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_subprocess_error(self):
        proc = _make_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ScrapeFailed) as exc:
                await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_success_false_raises_subprocess_error(self):
        payload = b'{"success": false, "error": "blocked"}'
        proc = _make_proc(stdout=payload)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ScrapeFailed) as exc:
                await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_timeout_raises_scrape_failed(self):
        proc = _make_proc()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        wait_for_path = "lyra.integrations.web_intel.asyncio.wait_for"
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(wait_for_path, side_effect=asyncio.TimeoutError):
                with pytest.raises(ScrapeFailed) as exc:
                    await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "timeout"

    @pytest.mark.asyncio
    async def test_empty_text_raises_subprocess_error(self):
        payload = b'{"success": true, "data": {"text": ""}}'
        proc = _make_proc(stdout=payload)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ScrapeFailed) as exc:
                await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_bad_json_raises_subprocess_error(self):
        proc = _make_proc(stdout=b"not json at all")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ScrapeFailed) as exc:
                await WebIntelScraper().scrape("https://example.com")
        assert exc.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_env_var_overrides_plugin_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LYRA_WEB_INTEL_PATH", str(tmp_path))
        scraper = WebIntelScraper()
        assert scraper._root == tmp_path
