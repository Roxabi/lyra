"""Tests for the new public Bus.inject(item) API (issue #534 Slice 1).

Both LocalBus and NatsBus must grow a public inject(item) method that wraps
self._staging.put_nowait(item).  This method does NOT exist yet — it will be
added in T7 (LocalBus) and T8 (NatsBus).

All tests below fail at runtime with AttributeError until then — this is the
expected RED state.

Note: tests also import from tests.helpers.messages which itself imports
AudioPayload (not yet defined) — if AudioPayload is missing, tests will fail
at collection time, which is also an acceptable RED state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from lyra.core.messaging.inbound_bus import LocalBus
from lyra.core.messaging.message import InboundMessage, Platform
from tests.helpers.messages import make_text_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nats_msg(item: InboundMessage) -> MagicMock:
    """Build a minimal NATS Msg mock pre-serialized from item."""
    import dataclasses

    d = dataclasses.asdict(item)
    # Normalize non-serializable fields
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    msg = MagicMock()
    msg.data = json.dumps(d).encode()
    return msg


# ---------------------------------------------------------------------------
# T4-1: LocalBus.inject() enqueues item directly to staging
# ---------------------------------------------------------------------------


class TestLocalBusInject:
    async def test_local_bus_inject_enqueues_item(self) -> None:
        # Arrange
        bus: LocalBus[InboundMessage] = LocalBus(name="test-inbound")
        msg = make_text_message()

        # Act — inject() is the new public method; does NOT require start()
        bus.inject(msg)

        # Assert — item is in the staging queue, retrievable via get()
        assert bus.staging_qsize() == 1
        retrieved = await asyncio.wait_for(bus.get(), timeout=1.0)
        assert retrieved is msg


# ---------------------------------------------------------------------------
# T4-2: NatsBus.inject() bypasses NATS and writes directly to staging
# ---------------------------------------------------------------------------


class TestNatsBusInject:
    async def test_nats_bus_inject_enqueues_item_bypassing_nats(self) -> None:
        # Arrange — NatsBus with a mock NATS client (no real connection needed)
        from lyra.nats.nats_bus import NatsBus

        nc = MagicMock()
        nc.publish = AsyncMock()
        bus: NatsBus[InboundMessage] = NatsBus(
            nc=nc,
            bot_id="testbot",
            item_type=InboundMessage,
            publish_only=False,
        )
        msg = make_text_message()

        # Act — inject() bypasses NATS pub/sub entirely
        bus.inject(msg)

        # Assert — item lands in the staging queue without any NATS publish
        assert bus.staging_qsize() == 1
        retrieved = await asyncio.wait_for(bus.get(), timeout=1.0)
        assert retrieved is msg
        nc.publish.assert_not_called()


# ---------------------------------------------------------------------------
# T4-3: inject() + normal NATS-deserialized message share the same queue
# ---------------------------------------------------------------------------


class TestBusInjectPreservesOrder:
    async def test_inject_preserves_order_with_nats_ingestion(self) -> None:
        # Arrange — LocalBus with two messages: one injected, one via put()
        bus: LocalBus[InboundMessage] = LocalBus(name="test-order")
        bus.register(Platform.TELEGRAM, maxsize=10)

        msg_a = make_text_message(id="msg-A", text="injected")
        msg_b = make_text_message(id="msg-B", text="via-put")

        # Start feeders so put() drains to staging
        await bus.start()
        try:
            # Act — inject msg_A first, then deliver msg_B via normal path
            bus.inject(msg_a)
            await bus.put(Platform.TELEGRAM, msg_b)

            # Wait for both to arrive in staging
            first = await asyncio.wait_for(bus.get(), timeout=1.0)
            second = await asyncio.wait_for(bus.get(), timeout=1.0)

            # Assert — both items arrived; inject()'d item is first
            assert {first.id, second.id} == {"msg-A", "msg-B"}
            assert first.id == "msg-A"
        finally:
            await bus.stop()
