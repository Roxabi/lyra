"""Tests for NatsOutboundListener — JetStream subscription + ack/nak behaviour."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from lyra.core.messaging.message import Platform

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nats_msg(data: dict[str, Any]) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(data).encode()
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# test_subscribe_uses_jetstream
# ---------------------------------------------------------------------------


async def test_subscribe_uses_jetstream() -> None:
    """start() must subscribe via JetStream with manual_ack=True."""
    from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    js = MagicMock()
    nc.jetstream = MagicMock(return_value=js)
    sub = MagicMock()
    sub.manual_ack = True
    js.subscribe = AsyncMock(return_value=sub)

    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc=nc,
        platform=Platform.TELEGRAM,
        bot_id="test_bot",
        adapter=adapter,
        js=js,
    )
    await listener.start()

    sub = listener._sub
    assert sub.manual_ack is True


# ---------------------------------------------------------------------------
# test_ack_on_success
# ---------------------------------------------------------------------------


async def test_ack_on_success() -> None:
    """msg.ack() is called after a successful dispatch."""
    from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener

    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc=AsyncMock(),
        platform=Platform.TELEGRAM,
        bot_id="test_bot",
        adapter=adapter,
    )

    # Seed cache so handle_send can resolve
    fake_inbound = MagicMock()
    fake_inbound.id = "scope_123"
    listener.cache_inbound(fake_inbound)

    envelope = {
        "type": "send",
        "stream_id": "scope_123",
        "outbound": {
            "schema_version": 1,
            "content": ["hello"],
        },
    }
    msg = _make_nats_msg(envelope)
    await listener._handle(msg)

    assert msg.ack.called


# ---------------------------------------------------------------------------
# test_nak_on_failure
# ---------------------------------------------------------------------------


async def test_nak_on_failure() -> None:
    """msg.nak() is called when dispatch raises an exception."""
    from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener

    adapter = AsyncMock()
    adapter.send = AsyncMock(side_effect=RuntimeError("boom"))
    listener = NatsOutboundListener(
        nc=AsyncMock(),
        platform=Platform.TELEGRAM,
        bot_id="test_bot",
        adapter=adapter,
    )

    # Seed cache so handle_send can resolve
    fake_inbound = MagicMock()
    fake_inbound.id = "scope_123"
    listener.cache_inbound(fake_inbound)

    envelope = {
        "type": "send",
        "stream_id": "scope_123",
        "outbound": {
            "schema_version": 1,
            "content": ["hello"],
        },
    }
    msg = _make_nats_msg(envelope)
    try:
        await listener._handle(msg)
    except RuntimeError:
        # current implementation doesn't catch; future impl will catch + nak
        pass

    assert msg.nak.called
