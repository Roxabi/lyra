"""Tests for the /session built-in command (session_commands.cmd_session)."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.commands import session_commands
from lyra.core.messaging.message import Response
from lyra.core.pool import Pool
from lyra.core.stores.turn_store_protocol import SessionRow
from lyra.infrastructure.stores.turn_store import TurnStore

from .conftest import make_message


def _make_pool(
    *,
    list_rows: list[dict] | None = None,
    current_cli: str | None = None,
    idle: bool = True,
    resume_accepted: bool = True,
) -> MagicMock:
    """Build a Pool mock with a pre-wired TurnStore mock."""
    store = MagicMock(spec=TurnStore)
    store.list_sessions = AsyncMock(return_value=list_rows or [])
    store.get_cli_session = AsyncMock(return_value=current_cli)

    pool = MagicMock(spec=Pool)
    pool.pool_id = "telegram:main:chat:1"
    pool.session_id = "sess-current"
    pool.turn_store = store
    pool.is_idle = idle
    pool.resume_session = AsyncMock(return_value=resume_accepted)
    return pool


_ROW_PING = {
    "session_id": "s1",
    "cli_session_id": "cli-1",
    "last_active_at": "2025-01-01T10:00:00+00:00",
    "first_user_msg": "ping",
    "turn_count": 3,
}


class TestSessionList:
    async def test_no_pool(self) -> None:
        msg = make_message(content="/session")
        resp = await session_commands.cmd_session(msg, [], None)
        assert isinstance(resp, Response)
        assert "no active pool" in resp.content.lower()

    async def test_list_requires_admin(self) -> None:
        pool = _make_pool(list_rows=[_ROW_PING])
        msg = make_message(content="/session", is_admin=False)
        resp = await session_commands.cmd_session(msg, [], pool)
        assert "admin-only" in resp.content.lower()
        pool.turn_store.list_sessions.assert_not_called()

    async def test_empty_list(self) -> None:
        pool = _make_pool(list_rows=[])
        msg = make_message(content="/session", is_admin=True)
        resp = await session_commands.cmd_session(msg, [], pool)
        assert "no past sessions" in resp.content.lower()

    async def test_list_marks_active_session(self) -> None:
        rows = [
            _ROW_PING,
            {
                "session_id": "s2",
                "cli_session_id": "cli-2",
                "last_active_at": "2024-12-30T10:00:00+00:00",
                "first_user_msg": "older",
                "turn_count": 1,
            },
        ]
        pool = _make_pool(list_rows=rows, current_cli="cli-2")
        msg = make_message(content="/session list", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["list"], pool)
        body = resp.content
        # Active row carries the marker, inactive row does not — assert both
        assert "► 2. older" in body
        assert "► 1." not in body
        assert "3 turns" in body

    async def test_list_no_turn_store(self) -> None:
        pool = _make_pool()
        pool.turn_store = None
        msg = make_message(content="/session", is_admin=True)
        resp = await session_commands.cmd_session(msg, [], pool)
        assert "not available" in resp.content.lower()


class TestSessionResume:
    async def test_resume_requires_admin(self) -> None:
        pool = _make_pool(list_rows=[_ROW_PING])
        msg = make_message(content="/session resume 1", is_admin=False)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "admin-only" in resp.content.lower()
        pool.resume_session.assert_not_called()

    async def test_resume_missing_index(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session resume", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume"], pool)
        assert "usage" in resp.content.lower()

    async def test_resume_non_int_index(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session resume foo", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "foo"], pool)
        # Static error: must NOT echo the raw token back to the chat
        assert "not a number" in resp.content.lower()
        assert "foo" not in resp.content.lower()

    async def test_resume_refused_when_pool_busy(self) -> None:
        pool = _make_pool(idle=False)
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "in flight" in resp.content.lower()
        pool.resume_session.assert_not_called()

    @pytest.mark.parametrize("idx_str", ["0", "-1", "999"])
    async def test_resume_index_out_of_range(self, idx_str: str) -> None:
        pool = _make_pool(list_rows=[_ROW_PING])
        msg = make_message(content=f"/session resume {idx_str}", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", idx_str], pool)
        assert "out of range" in resp.content.lower()
        pool.resume_session.assert_not_called()

    async def test_resume_target_without_cli_session_id(self) -> None:
        pool = _make_pool(
            list_rows=[
                {
                    **_ROW_PING,
                    "cli_session_id": None,
                    "first_user_msg": "x",
                    "turn_count": 0,
                }
            ]
        )
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "no cli session id" in resp.content.lower()
        pool.resume_session.assert_not_called()

    async def test_resume_backend_refused(self) -> None:
        pool = _make_pool(list_rows=[_ROW_PING], resume_accepted=False)
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "refused" in resp.content.lower()
        pool.resume_session.assert_called_once_with("cli-1")

    async def test_resume_success(self) -> None:
        pool = _make_pool(list_rows=[_ROW_PING])
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        assert "Resumed #1" in resp.content
        assert "ping" in resp.content
        pool.resume_session.assert_called_once_with("cli-1")

    async def test_resume_truncates_long_title(self) -> None:
        """Long first_user_msg must be truncated with `…` in the confirmation."""
        long_msg = "fix the auth bug that randomly logs users out after 24h sessions"
        pool = _make_pool(list_rows=[{**_ROW_PING, "first_user_msg": long_msg}])
        msg = make_message(content="/session resume 1", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["resume", "1"], pool)
        # _cmd_resume uses _truncate(text, 40); 64-char input must be cut and end with …
        assert "Resumed #1" in resp.content
        assert "…" in resp.content
        assert long_msg not in resp.content


class TestSessionUnknown:
    async def test_unknown_subcommand(self) -> None:
        pool = _make_pool()
        msg = make_message(content="/session blarg", is_admin=True)
        resp = await session_commands.cmd_session(msg, ["blarg"], pool)
        assert "unknown subcommand" in resp.content.lower()


class TestFormatHelpers:
    """Direct unit tests for the formatting helpers in session_commands."""

    # ── _truncate ──────────────────────────────────────────────────────────

    def test_truncate_none(self) -> None:
        assert session_commands._truncate(None) == "(empty)"

    def test_truncate_empty(self) -> None:
        assert session_commands._truncate("") == "(empty)"

    def test_truncate_whitespace_only(self) -> None:
        assert session_commands._truncate("   \n\t  ") == "(empty)"

    def test_truncate_below_cap(self) -> None:
        assert session_commands._truncate("hello", n=10) == "hello"

    def test_truncate_at_cap(self) -> None:
        s = "x" * 60
        assert session_commands._truncate(s, n=60) == s

    def test_truncate_over_cap(self) -> None:
        s = "x" * 61
        out = session_commands._truncate(s, n=60)
        assert out.endswith("…")
        assert len(out) == 60

    def test_truncate_multiline_keeps_first_line(self) -> None:
        out = session_commands._truncate("first line\nsecond line\nthird", n=60)
        assert out == "first line"

    # ── _format_age ────────────────────────────────────────────────────────

    def test_format_age_none(self) -> None:
        assert session_commands._format_age(None) == "?"

    def test_format_age_malformed(self) -> None:
        assert session_commands._format_age("not-an-iso-date") == "?"

    def test_format_age_future(self) -> None:
        # Far-future ts must not produce "-Ns ago" (fix for blocker #15)
        assert session_commands._format_age("2999-01-01T00:00:00+00:00") == "?"

    def test_format_age_naive_timestamp_treated_as_utc(self) -> None:
        # ISO without tz should be assumed UTC, not crash
        out = session_commands._format_age("2025-01-01T00:00:00")
        assert out.endswith("ago")

    def test_format_age_brackets(self) -> None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        # Pick a value strictly inside each bracket so the choice is unambiguous
        cases = [
            (timedelta(seconds=5), "s ago"),
            (timedelta(minutes=5), "m ago"),
            (timedelta(hours=5), "h ago"),
            (timedelta(days=5), "d ago"),
        ]
        for delta, suffix in cases:
            ts = (now - delta).isoformat()
            assert session_commands._format_age(ts).endswith(suffix), (delta, suffix)

    # ── _format_list ───────────────────────────────────────────────────────

    def test_format_list_empty(self) -> None:
        assert session_commands._format_list([], current_cli=None) == (
            "No past sessions for this chat."
        )

    def test_format_list_marks_active_only(self) -> None:
        rows = cast(
            list[SessionRow],
            [
                {**_ROW_PING, "session_id": "s1", "cli_session_id": "cli-1"},
                {
                    **_ROW_PING,
                    "session_id": "s2",
                    "cli_session_id": "cli-2",
                    "first_user_msg": "older",
                },
            ],
        )
        body = session_commands._format_list(rows, current_cli="cli-2")
        assert "► 2." in body
        assert "► 1." not in body
