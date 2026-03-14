"""Tests for session_helpers.py (issue #99 T3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.session_helpers import (
    ScrapeFailed,
    VaultWriteFailed,
    scrape_url,
    vault_add,
    vault_search,
)


def _make_fake_proc(returncode: int = 0, stdout: bytes = b"result text") -> MagicMock:
    """Return a mock asyncio subprocess with communicate() already set."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class TestScrapeUrl:
    """scrape_url() — happy path, not_available, subprocess_error."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_stdout(self) -> None:
        proc = _make_fake_proc(returncode=0, stdout=b"scraped content here")
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await scrape_url("https://example.com")
        assert result == "scraped content here"

    @pytest.mark.asyncio
    async def test_file_not_found_raises_scrape_failed_not_available(self) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("web-intel:scrape"),
        ):
            with pytest.raises(ScrapeFailed) as exc_info:
                await scrape_url("https://example.com")
        assert exc_info.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_scrape_failed_subprocess_error(self) -> None:
        proc = _make_fake_proc(returncode=1)
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            with pytest.raises(ScrapeFailed) as exc_info:
                await scrape_url("https://example.com")
        assert exc_info.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_url_passed_to_subprocess(self) -> None:
        proc = _make_fake_proc(stdout=b"content")
        url = "https://example.com/page"
        calls: list = []

        async def _fake_exec(*args, **kwargs):
            calls.append(args)
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            await scrape_url(url)

        assert len(calls) == 1
        assert calls[0][0] == "web-intel:scrape"
        assert calls[0][1] == url


class TestVaultAdd:
    """vault_add() — happy path, not_available, subprocess_error."""

    @pytest.mark.asyncio
    async def test_happy_path_completes_without_raising(self) -> None:
        proc = _make_fake_proc(returncode=0)
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            await vault_add("My Title", ["tag1", "tag2"], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_file_not_found_raises_vault_write_failed(self) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("vault"),
        ):
            with pytest.raises(VaultWriteFailed):
                await vault_add("T", [], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_vault_write_failed(self) -> None:
        proc = _make_fake_proc(returncode=1)
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            with pytest.raises(VaultWriteFailed):
                await vault_add("T", [], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_tags_included_in_args_when_provided(self) -> None:
        proc = _make_fake_proc(returncode=0)
        calls: list = []

        async def _fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            await vault_add("T", ["a", "b"], "https://x.com", "body")

        joined_args = " ".join(calls[0])
        assert "--tags" in joined_args
        assert "a,b" in joined_args

    @pytest.mark.asyncio
    async def test_tags_omitted_when_empty(self) -> None:
        proc = _make_fake_proc(returncode=0)
        calls: list = []

        async def _fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=_fake_exec):
            await vault_add("T", [], "https://x.com", "body")

        joined_args = " ".join(calls[0])
        assert "--tags" not in joined_args


class TestVaultSearch:
    """vault_search() — happy path, not_available, nonzero exit."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_stdout(self) -> None:
        proc = _make_fake_proc(returncode=0, stdout=b"result 1\nresult 2\n")
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await vault_search("python asyncio")
        assert "result 1" in result

    @pytest.mark.asyncio
    async def test_file_not_found_returns_graceful_string(self) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("vault"),
        ):
            result = await vault_search("query")
        assert "not available" in result.lower() or "vault cli" in result.lower()

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_no_results_string(self) -> None:
        proc = _make_fake_proc(returncode=1)
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await vault_search("query")
        assert "no results" in result.lower()
