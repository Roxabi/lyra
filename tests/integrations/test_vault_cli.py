"""Tests for VaultCli (lyra.integrations.vault_cli)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.integrations.base import VaultProvider, VaultWriteFailed
from lyra.integrations.vault_cli import VaultCli


def _make_proc(returncode=0, stdout=b'', stderr=b''):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc

class TestVaultCliAdd:
    def test_implements_vault_provider(self):
        assert isinstance(VaultCli(), VaultProvider)

    @pytest.mark.asyncio
    async def test_happy_path_completes(self):
        proc = _make_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            await VaultCli().add("Title", ["t1"], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_uses_vault_put_command(self):
        proc = _make_proc(returncode=0)
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc
        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await VaultCli().add("T", ["a", "b"], "https://x.com", "body")
        assert calls[0][0] == "vault"
        assert calls[0][1] == "put"

    @pytest.mark.asyncio
    async def test_tags_in_metadata_json(self):
        proc = _make_proc(returncode=0)
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc
        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await VaultCli().add("T", ["a", "b"], "https://x.com", "body")
        joined = " ".join(calls[0])
        assert "--metadata" in joined
        assert '"a"' in joined
        assert '"b"' in joined

    @pytest.mark.asyncio
    async def test_file_not_found_raises(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(VaultWriteFailed):
                await VaultCli().add("T", [], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self):
        proc = _make_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(VaultWriteFailed):
                await VaultCli().add("T", [], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_timeout_raises_vault_write_failed(self):
        proc = _make_proc()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        wait_for_path = "lyra.integrations.vault_cli.asyncio.wait_for"
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(wait_for_path, side_effect=asyncio.TimeoutError):
                with pytest.raises(VaultWriteFailed):
                    await VaultCli().add("T", [], "https://x.com", "body")

    @pytest.mark.asyncio
    async def test_empty_tags_not_in_metadata_json(self):
        proc = _make_proc(returncode=0)
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc
        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await VaultCli().add("T", [], "https://x.com", "body")
        meta_idx = calls[0].index("--metadata")
        metadata = json.loads(calls[0][meta_idx + 1])
        assert "tags" not in metadata

    @pytest.mark.asyncio
    async def test_no_metadata_flag_when_url_and_tags_both_empty(self):
        """Regression: --metadata must be omitted when url='' and tags=[]."""
        proc = _make_proc(returncode=0)
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc
        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await VaultCli().add("T", [], "", "body")
        assert "--metadata" not in calls[0]

class TestVaultCliSearch:
    @pytest.mark.asyncio
    async def test_happy_path_returns_stdout(self):
        proc = _make_proc(returncode=0, stdout=b"result 1\nresult 2\n")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await VaultCli().search("python asyncio")
        assert "result 1" in result

    @pytest.mark.asyncio
    async def test_file_not_found_returns_graceful(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await VaultCli().search("query")
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_nonzero_returns_no_results(self):
        proc = _make_proc(returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await VaultCli().search("query")
        assert "no results" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_returns_graceful_string(self):
        proc = _make_proc()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        wait_for_path = "lyra.integrations.vault_cli.asyncio.wait_for"
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(wait_for_path, side_effect=asyncio.TimeoutError):
                result = await VaultCli().search("query")
        assert "timed out" in result.lower()
