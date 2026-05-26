"""Tests for TypingListener (#1376) — AC1, AC4.

RED phase: TypingListener may not yet exist when this file lands. Verify
re-runs in Wave 2 once T6 lands listener.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.transport.typing_event import TypingEvent
from lyra.transport.work_scope import WorkScope
from lyra.typing.listener import TypingListener


def _make_msg(event: TypingEvent) -> MagicMock:
    m = MagicMock()
    m.data = event.model_dump_json().encode("utf-8")
    return m


async def _async_noop() -> None:
    pass


@pytest.mark.asyncio
async def test_dispatch_started_calls_manager_start_with_factory() -> None:
    nc = AsyncMock()
    mgr = MagicMock()
    resolver = lambda scope: scope.scope_id  # noqa: E731
    built_factories: list[int] = []

    def builder(target: int):
        built_factories.append(target)

        async def _factory() -> None:
            pass

        return _factory

    listener = TypingListener(
        nc, "lyra.typing.discord.x", resolver, builder, mgr, enabled=True
    )
    scope = WorkScope(platform="discord", bot_id="x", scope_id=42, trace_id="t")
    await listener._on_msg(_make_msg(TypingEvent(kind="started", scope=scope, ts=1.0)))
    mgr.start.assert_called_once()
    args, _ = mgr.start.call_args
    assert args[0] == 42  # AC1 — resolved target
    assert callable(args[1])  # AC1 — coro_factory present
    assert built_factories == [42]  # factory_builder called with target


@pytest.mark.asyncio
async def test_dispatch_ended_calls_manager_cancel() -> None:
    nc = AsyncMock()
    mgr = MagicMock()
    listener = TypingListener(
        nc,
        "s",
        lambda s: s.scope_id,
        lambda t: _async_noop,
        mgr,
        enabled=True,
    )
    scope = WorkScope(platform="telegram", bot_id="x", scope_id=7, trace_id="t")
    await listener._on_msg(_make_msg(TypingEvent(kind="ended", scope=scope, ts=1.0)))
    mgr.cancel.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_resolver_exception_no_defensive_cancel() -> None:
    nc = AsyncMock()
    mgr = MagicMock()

    def bad_resolver(scope):
        raise RuntimeError("boom")

    listener = TypingListener(
        nc,
        "s",
        bad_resolver,
        lambda t: _async_noop,
        mgr,
        enabled=True,
    )
    scope = WorkScope(platform="discord", bot_id="x", scope_id=1, trace_id="t")
    await listener._on_msg(_make_msg(TypingEvent(kind="started", scope=scope, ts=1.0)))
    mgr.cancel.assert_not_called()  # target never resolved → no defensive cancel


@pytest.mark.asyncio
async def test_factory_builder_exception_triggers_defensive_cancel() -> None:
    nc = AsyncMock()
    mgr = MagicMock()

    def bad_builder(target):
        raise RuntimeError("boom")

    listener = TypingListener(
        nc,
        "s",
        lambda s: s.scope_id,
        bad_builder,
        mgr,
        enabled=True,
    )
    scope = WorkScope(platform="discord", bot_id="x", scope_id=99, trace_id="t")
    await listener._on_msg(_make_msg(TypingEvent(kind="started", scope=scope, ts=1.0)))
    mgr.cancel.assert_called_once_with(99)


@pytest.mark.asyncio
async def test_flag_off_no_subscribe() -> None:
    nc = AsyncMock()
    listener = TypingListener(
        nc,
        "s",
        lambda s: s.scope_id,
        lambda t: _async_noop,
        MagicMock(),
        enabled=False,
    )
    await listener.start()
    nc.subscribe.assert_not_called()  # AC4
