"""Unit tests for TurnQueryServer (#2309)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.infrastructure.turn_writer.query import TurnQueryServer
from roxabi_contracts.turns import SUBJECTS


def _msg(
    subject: str, data: dict | bytes | None, *, reply: str = "_INBOX.test"
) -> MagicMock:
    m = MagicMock()
    m.subject = subject
    m.reply = reply
    if data is None:
        m.data = b""
    elif isinstance(data, bytes):
        m.data = data
    else:
        m.data = json.dumps(data).encode()
    m.respond = AsyncMock()
    return m


@pytest.mark.anyio
async def test_get_cli_session_happy() -> None:
    store = AsyncMock()
    store.get_cli_session = AsyncMock(return_value="cli-abc")
    nc = AsyncMock()
    server = TurnQueryServer(store, nc)

    msg = _msg(SUBJECTS.get_cli_session, {"session_id": "s1"})
    await server._on_get_cli_session(msg)

    store.get_cli_session.assert_awaited_once_with("s1")
    payload = json.loads(msg.respond.await_args.args[0].decode())
    assert payload == {"cli_session_id": "cli-abc"}


@pytest.mark.anyio
async def test_get_cli_session_missing() -> None:
    store = AsyncMock()
    store.get_cli_session = AsyncMock(return_value=None)
    server = TurnQueryServer(store, AsyncMock())

    msg = _msg(SUBJECTS.get_cli_session, {"session_id": "unknown"})
    await server._on_get_cli_session(msg)

    payload = json.loads(msg.respond.await_args.args[0].decode())
    assert payload == {"cli_session_id": None}


@pytest.mark.anyio
async def test_get_cli_session_bad_json() -> None:
    store = AsyncMock()
    server = TurnQueryServer(store, AsyncMock())
    msg = _msg(SUBJECTS.get_cli_session, b"not-json")
    await server._on_get_cli_session(msg)
    payload = json.loads(msg.respond.await_args.args[0].decode())
    assert payload["error"]["code"] == "bad_request"
    store.get_cli_session.assert_not_awaited()


@pytest.mark.anyio
async def test_get_resume_count() -> None:
    store = AsyncMock()
    store.get_resume_count = AsyncMock(return_value=3)
    server = TurnQueryServer(store, AsyncMock())
    msg = _msg(SUBJECTS.get_resume_count, {"session_id": "s1"})
    await server._on_get_resume_count(msg)
    payload = json.loads(msg.respond.await_args.args[0].decode())
    assert payload == {"resume_count": 3}


@pytest.mark.anyio
async def test_start_subscribes_all_subjects() -> None:
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=MagicMock())
    server = TurnQueryServer(AsyncMock(), nc)
    await server.start()
    assert nc.subscribe.await_count == 8
    subjects = {c.args[0] for c in nc.subscribe.await_args_list}
    assert SUBJECTS.get_cli_session in subjects
    assert SUBJECTS.get_turns_by_session in subjects
