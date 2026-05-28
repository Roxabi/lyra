"""Tests for NatsOutboundListener — JetStream subscription + ack/nak behaviour."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.mark.asyncio
async def test_subscribe_uses_jetstream() -> None:
    """start() must subscribe via JetStream with explicit ack and max_deliver=3."""
    from nats.js.api import AckPolicy, ConsumerConfig

    from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    js = MagicMock()
    nc.jetstream = MagicMock(return_value=js)
    sub = MagicMock()
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

    _, call_kwargs = js.subscribe.call_args
    assert isinstance(call_kwargs["config"], ConsumerConfig)
    assert call_kwargs["config"].ack_policy is AckPolicy.EXPLICIT
    assert call_kwargs["config"].max_deliver == 3


# ---------------------------------------------------------------------------
# test_ack_on_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
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
    assert not msg.nak.called


# ---------------------------------------------------------------------------
# test_nak_on_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
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
    with pytest.raises(RuntimeError, match="boom"):
        await listener._handle(msg)

    assert msg.nak.called
