"""AC1 end-to-end — TypingPublisher -> NATS Core -> TypingListener round-trip."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from nats.aio.client import Client as NATS

import nats
from lyra.transport.typing_publisher import TypingPublisher
from lyra.transport.work_scope import WorkScope
from lyra.typing.listener import TypingListener
from tests.nats.conftest import requires_nats_server


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll ``predicate`` until true or ``timeout`` elapses.

    Deterministic replacement for a fixed ``asyncio.sleep`` when waiting on an
    async round-trip (publish -> NATS Core -> listener callback): returns as
    soon as the condition holds, and tolerates a slow runner up to ``timeout``.
    On timeout it simply returns so the caller's assertion produces the failure.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            return
        await asyncio.sleep(0.01)


@requires_nats_server
@pytest.mark.asyncio
async def test_publisher_to_listener_e2e_discord(nats_server_url: str) -> None:
    """Publisher emits started/ended; listener invokes manager.start/cancel."""
    nc_pub: NATS = await nats.connect(nats_server_url)
    nc_sub: NATS = await nats.connect(nats_server_url)

    mgr = MagicMock()

    def resolver(scope: WorkScope) -> int:
        return scope.scope_id

    def builder(target: int):
        async def _f() -> None:
            return None

        return _f

    listener = TypingListener(
        nc_sub,
        "lyra.typing.discord.x",
        resolver,
        builder,
        mgr,
        enabled=True,
    )
    await listener.start()
    # NATS Core drops messages with no registered subscriber. subscribe() only
    # buffers the SUB locally; flush round-trips PING/PONG so the server has
    # registered it before the publisher (separate connection) emits anything.
    await nc_sub.flush()

    pub = TypingPublisher(nc_pub, enabled=True)
    scope = WorkScope(platform="discord", bot_id="x", scope_id=42, trace_id="t")

    await pub.publish_started(scope)
    await nc_pub.flush()  # publish() buffers locally; flush forces it to the server
    await _wait_until(lambda: mgr.start.called)
    mgr.start.assert_called_once()
    assert mgr.start.call_args[0][0] == 42

    await pub.publish_ended(scope)
    await nc_pub.flush()
    await _wait_until(lambda: mgr.cancel.called)
    mgr.cancel.assert_called_once_with(42)

    await listener.stop()
    await nc_pub.close()
    await nc_sub.close()
