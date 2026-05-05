"""Tests for the /session built-in command (session_commands.cmd_session)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.commands import session_commands
from lyra.core.messaging.message import Response
from lyra.core.pool import Pool

from .conftest import make_message


def _make_pool(
    *,
    list_rows: list[dict] | None = None,
    current_cli: str | None = None,
    idle: bool = True,
    resume_accepted: bool = True,
) -> MagicMock:
    """Build a Pool mock with a pre-wired TurnStore mock."""
    store = MagicMock()
    store.list_sessions = AsyncMock(return_value=list_rows or [])
    store.get_cli_session = AsyncMock(return_value=current_cli)

    pool = MagicMock(spec=Pool)
    pool.pool_id = "telegram:main:chat:1"
    pool.session_id = "sess-current"
    pool.turn_store = store
    pool.is_idle = idle
    pool.resume_session = AsyncMock(return_value=resume_accepted)
    return pool


class TestSessionList:
    @pytest.mark.asyncio
    async def test_no_pool(self) -> None:
        msg = make_message(content="/session")
        resp = await session_commands.cmd_session(msg, [], None)
        assert isinstance(resp, Response)
        assert "no active pool" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        pool = _make_pool(list_rows=[])
        msg = make_message(content="/session")
        resp = await session_commands.cmd_session(msg, [], pool)
        assert "no past sessions" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_list_marks_active_session(self) -> None:
        rows = [
            {
                "session_id": "s1",
                "cli_session_id": "cli-1",
                "last_active_at": "2025-01-01T10:00:00+00:00",
                "first_user_msg": "ping",
                "turn_count": 3,
            },
            {
                "session_id": "s2",
                "cli_session_id": "cli-2",
                "last_active_at": "2024-12-30T10:00:00+00:00",
                "first_user_msg": "older",
                "turn_count": 1,
            },
        ]
        pool = _make_pool(list_rows=rows, current_cli="cli-2")
        msg = make_message(content="/session list")
        resp = await session_commands.cmd_session(msg, ["list"], pool)
        # The marker ► precedes the active row only
        body = resp.content
        assert "► 2. older" in body
        assert "  1. ping" in body
        assert "3 turns" in body

    @pytest.mark.asyncio
    async def test_list_no_turn_store(self) -> None:
        pool = _make_pool()
        pool.turn_store = None
        msg = make_message(content="/session")
        resp = await session_commands.cmd_session(msg, [], pool)
        assert "not available" in resp.content.lower()


class TestSessionResume:
    @pytest.mark.asyncio
    async def test_resume_requires_admin(self) -> None:
        pool = _make_pool(
            list_rows=[
                {
                    "session_id": "s1",
                    "cli_session_id": "cli-1",
                    "last_active_at": "2025-01-01T10:00:00+00:00",
                    "first_user_msg": "x",
                    "turn_count": 1,
                }
            ]
        )
        msg = make_message(content="/session resume 1", is_admin=False)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "admin-only" in resp.content.lower()
        pool.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_missing_index(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session resume", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume"], pool)
        assert "usage" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_resume_non_int_index(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session resume foo", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "foo"], pool)
        assert "not a number" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_resume_refused_when_pool_busy(self) -> None:
        pool = _make_pool(idle=False)
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "in flight" in resp.content.lower()
        pool.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_index_out_of_range(self) -> None:
        pool = _make_pool(list_rows=[])
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "out of range" in resp.content.lower()
        pool.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_target_without_cli_session_id(self) -> None:
        pool = _make_pool(
            list_rows=[
                {
                    "session_id": "s1",
                    "cli_session_id": None,
                    "last_active_at": "2025-01-01T10:00:00+00:00",
                    "first_user_msg": "x",
                    "turn_count": 0,
                }
            ]
        )
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "no cli session id" in resp.content.lower()
        pool.resume_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_backend_refused(self) -> None:
        pool = _make_pool(
            list_rows=[
                {
                    "session_id": "s1",
                    "cli_session_id": "cli-1",
                    "last_active_at": "2025-01-01T10:00:00+00:00",
                    "first_user_msg": "x",
                    "turn_count": 1,
                }
            ],
            resume_accepted=False,
        )
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "refused" in resp.content.lower()
        pool.resume_session.assert_called_once_with("cli-1")

    @pytest.mark.asyncio
    async def test_resume_success(self) -> None:
        pool = _make_pool(
            list_rows=[
                {
                    "session_id": "s1",
                    "cli_session_id": "cli-1",
                    "last_active_at": "2025-01-01T10:00:00+00:00",
                    "first_user_msg": "fix the auth bug",
                    "turn_count": 5,
                }
            ]
        )
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "Resumed #1" in resp.content
        assert "fix the auth bug" in resp.content
        pool.resume_session.assert_called_once_with("cli-1")


class TestSessionUnknown:
    @pytest.mark.asyncio
    async def test_unknown_subcommand(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session blarg")
        resp = await session_commands.cmd_session(msg, ["blarg"], pool)
        assert "unknown subcommand" in resp.content.lower()
