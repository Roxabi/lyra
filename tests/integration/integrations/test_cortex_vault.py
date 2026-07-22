"""Tests for CortexVault (VaultProvider over NATS contracts)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.exceptions import VaultWriteFailed
from factory.integrations.base import VaultProvider
from factory.integrations.cortex_vault import CortexVault
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.memory import (
    AssembleResponse,
    CaptureResponse,
    SearchHit,
    SearchResponse,
)


def _env(request_id: str = "r1") -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "t1",
        "issued_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        "job_id": "job1",
        "request_id": request_id,
    }


def test_implements_vault_provider() -> None:
    assert isinstance(CortexVault(), VaultProvider)


@pytest.mark.asyncio
async def test_add_success() -> None:
    resp = CaptureResponse(**_env(), ok=True, entry_id=9)
    msg = MagicMock()
    msg.data = resp.model_dump_json().encode()
    nc = AsyncMock()
    nc.request = AsyncMock(return_value=msg)
    vault = CortexVault(nc=nc)
    await vault.add("T", ["a"], "https://x.com", "body")
    nc.request.assert_awaited_once()
    subject = nc.request.await_args.args[0]
    assert subject == "roxabi.memory.capture"


@pytest.mark.asyncio
async def test_add_not_ok_raises() -> None:
    resp = CaptureResponse(**_env(), ok=False, error="boom")
    msg = MagicMock()
    msg.data = resp.model_dump_json().encode()
    nc = AsyncMock()
    nc.request = AsyncMock(return_value=msg)
    with pytest.raises(VaultWriteFailed):
        await CortexVault(nc=nc).add("T", [], "", "body")


@pytest.mark.asyncio
async def test_search_formats_hits() -> None:
    resp = SearchResponse(
        **_env(),
        ok=True,
        hits=[
            SearchHit(
                entry_id=1,
                title="Hello",
                category="knowledge",
                entry_type="twitter",
                snippet="snippet",
                url="https://x.com/1",
            )
        ],
    )
    msg = MagicMock()
    msg.data = resp.model_dump_json().encode()
    nc = AsyncMock()
    nc.request = AsyncMock(return_value=msg)
    text = await CortexVault(nc=nc).search("hello")
    assert "Hello" in text
    assert "knowledge/twitter" in text
    assert "https://x.com/1" in text


@pytest.mark.asyncio
async def test_search_swallows_errors() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError())
    assert await CortexVault(nc=nc).search("x") == ""


@pytest.mark.asyncio
async def test_assemble_returns_text() -> None:
    resp = AssembleResponse(
        **_env(),
        ok=True,
        text="### Note\nbody",
        tokens_used=10,
    )
    msg = MagicMock()
    msg.data = resp.model_dump_json().encode()
    nc = AsyncMock()
    nc.request = AsyncMock(return_value=msg)
    text = await CortexVault(nc=nc).assemble(goal="Note")
    assert "Note" in text
    subject = nc.request.await_args.args[0]
    assert subject == "roxabi.memory.query.assemble"


@pytest.mark.asyncio
async def test_assemble_fail_open() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError())
    assert await CortexVault(nc=nc).assemble(goal="x") == ""
