"""Unit tests for TurnQueryClient (#2309)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.errors import NoRespondersError

from factory.transport.turn_query_client import TurnQueryClient
from roxabi_contracts.turns import SUBJECTS


def _reply(payload: dict) -> MagicMock:
    m = MagicMock()
    m.data = json.dumps(payload).encode()
    return m


@pytest.mark.anyio
async def test_get_cli_session_ok() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(return_value=_reply({"cli_session_id": "cli-1"}))
    client = TurnQueryClient(nc, timeout=1.0)
    assert await client.get_cli_session("s1") == "cli-1"
    nc.request.assert_awaited_once()
    args = nc.request.await_args
    assert args.args[0] == SUBJECTS.get_cli_session
    assert json.loads(args.args[1]) == {"session_id": "s1"}


@pytest.mark.anyio
async def test_get_cli_session_timeout_fail_soft() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError())
    client = TurnQueryClient(nc)
    assert await client.get_cli_session("s1") is None


@pytest.mark.anyio
async def test_get_cli_session_no_responders_fail_soft() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=NoRespondersError())
    client = TurnQueryClient(nc)
    assert await client.get_cli_session("s1") is None


@pytest.mark.anyio
async def test_get_resume_count_fail_soft_zero() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(side_effect=TimeoutError())
    client = TurnQueryClient(nc)
    assert await client.get_resume_count("s1") == 0


@pytest.mark.anyio
async def test_list_sessions_error_envelope() -> None:
    nc = AsyncMock()
    nc.request = AsyncMock(
        return_value=_reply({"error": {"code": "internal_error", "message": "x"}})
    )
    client = TurnQueryClient(nc)
    assert await client.list_sessions("pool-1") == []


@pytest.mark.anyio
async def test_get_turns_by_session_ok() -> None:
    nc = AsyncMock()
    turns = [{"id": 1, "content": "hi", "role": "user"}]
    nc.request = AsyncMock(return_value=_reply({"turns": turns}))
    client = TurnQueryClient(nc)
    assert await client.get_turns_by_session("s1", limit=10) == turns
