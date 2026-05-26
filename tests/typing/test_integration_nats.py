"""AC1 end-to-end — TypingPublisher -> NATS Core -> TypingListener round-trip."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from nats.aio.client import Client as NATS

import nats
from lyra.transport.typing_publisher import TypingPublisher
from lyra.transport.work_scope import WorkScope
from lyra.typing.listener import TypingListener
from tests.nats.conftest import requires_nats_server


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

    pub = TypingPublisher(nc_pub, enabled=True)
    scope = WorkScope(platform="discord", bot_id="x", scope_id=42, trace_id="t")

    await pub.publish_started(scope)
    await asyncio.sleep(0.1)
    mgr.start.assert_called_once()
    assert mgr.start.call_args[0][0] == 42

    await pub.publish_ended(scope)
    await asyncio.sleep(0.1)
    mgr.cancel.assert_called_once_with(42)

    await listener.stop()
    await nc_pub.close()
    await nc_sub.close()
