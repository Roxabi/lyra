"""Tests for make_typing_factory (#1396).

T1 — direct round-trip: builder(scope_id)() invokes worker_fn(scope_id).
T2 — multi-bot regression: per-iteration partial binding stays per-instance
(guards against the late-binding lambda bug the PR replaced).
"""

from functools import partial
from typing import Any
from unittest.mock import MagicMock

import pytest

from factory.typing import make_typing_factory


@pytest.mark.asyncio
async def test_factory_builder_forwards_scope_id() -> None:
    """T1: builder(42)() awaits worker_fn(42) exactly once."""
    seen: list[int] = []

    async def worker(scope_id: int) -> None:
        seen.append(scope_id)

    factory_builder = make_typing_factory(worker)
    coro_factory = factory_builder(42)
    await coro_factory()

    assert seen == [42]


@pytest.mark.asyncio
async def test_factory_builder_independent_per_scope() -> None:
    """T1: distinct scope_ids produce independent closures."""
    seen: list[int] = []

    async def worker(scope_id: int) -> None:
        seen.append(scope_id)

    factory_builder = make_typing_factory(worker)
    await factory_builder(1)()
    await factory_builder(2)()
    await factory_builder(1)()

    assert seen == [1, 2, 1]


@pytest.mark.asyncio
async def test_factory_builder_does_not_call_worker_eagerly() -> None:
    """T1: factory_builder(sid) returns a coro_factory; the worker is only
    invoked when coro_factory() is called and awaited."""
    seen: list[int] = []

    async def worker(scope_id: int) -> None:
        seen.append(scope_id)

    factory_builder = make_typing_factory(worker)
    coro_factory = factory_builder(99)
    assert seen == []  # builder did not call worker

    coro = coro_factory()
    assert seen == []  # creating coroutine did not call worker

    await coro
    assert seen == [99]  # only the await fires worker


@pytest.mark.asyncio
async def test_multi_bot_partial_binding_stays_per_instance() -> None:
    """T2: per-iteration `partial(worker, adapter.attr)` captures the
    iteration's adapter, not the last loop adapter (regression guard for the
    late-binding lambda bug the PR fixed in adapter_standalone.py)."""
    bots = [MagicMock(name="bot0"), MagicMock(name="bot1"), MagicMock(name="bot2")]
    calls: list[tuple[Any, int]] = []

    async def worker(bot: Any, scope_id: int) -> None:
        calls.append((bot, scope_id))

    factory_builders = []
    for bot in bots:
        adapter = MagicMock()
        adapter.bot = bot  # mirrors `adapter.bot` evaluated at partial() time
        factory_builders.append(make_typing_factory(partial(worker, adapter.bot)))

    for i, fb in enumerate(factory_builders):
        await fb(100 + i)()

    assert calls == [
        (bots[0], 100),
        (bots[1], 101),
        (bots[2], 102),
    ]


@pytest.mark.asyncio
async def test_worker_exception_propagates_at_coro_factory_invocation() -> None:
    """T1: worker raising synchronously surfaces when the returned coro_factory
    is called (mirrors what TypingTaskManager would observe)."""

    async def worker(scope_id: int) -> None:
        raise RuntimeError(f"boom {scope_id}")

    factory_builder = make_typing_factory(worker)
    coro_factory = factory_builder(7)

    with pytest.raises(RuntimeError, match="boom 7"):
        await coro_factory()
