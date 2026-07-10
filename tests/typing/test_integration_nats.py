"""AC1 end-to-end — TypingPublisher -> NATS Core -> TypingListener round-trip."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import nats
from factory.transport.typing_publisher import TypingPublisher
from factory.transport.work_scope import WorkScope
from factory.typing.listener import TypingListener
from tests.nats.conftest import requires_nats_server

pytestmark = [
    pytest.mark.subprocess_nats,
    pytest.mark.xdist_group(name="nats_server"),
]


@requires_nats_server
@pytest.mark.asyncio
async def test_publisher_to_listener_e2e_discord(nats_server_url: str) -> None:
    """Publisher emits started/ended; listener invokes manager.start/cancel."""
    nc_pub = await nats.connect(nats_server_url)
    nc_sub = await nats.connect(nats_server_url)

    started = asyncio.Event()
    ended = asyncio.Event()

    mgr = MagicMock()

    def _on_start(target: int, _factory) -> None:
        started.set()

    def _on_cancel(target: int) -> None:
        ended.set()

    mgr.start.side_effect = _on_start
    mgr.cancel.side_effect = _on_cancel

    def resolver(scope: WorkScope) -> int:
        return scope.scope_id

    def builder(target: int):
        async def _f() -> None:
            return None

        return _f

    listener = TypingListener(
        nc_sub,
        "factory.typing.discord.x",
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
    await asyncio.wait_for(started.wait(), timeout=30.0)
    mgr.start.assert_called_once()
    assert mgr.start.call_args[0][0] == 42

    await pub.publish_ended(scope)
    await nc_pub.flush()
    await asyncio.wait_for(ended.wait(), timeout=30.0)
    mgr.cancel.assert_called_once_with(42)

    await listener.stop()
    await nc_pub.close()
    await nc_sub.close()
