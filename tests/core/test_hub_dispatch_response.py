"""Tests for Hub dispatch_response and dispatch_attachment methods."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from lyra.core import Hub, Response
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
    Platform,
)
from tests.core.conftest import make_inbound_message

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

# ---------------------------------------------------------------------------
# T5 — dispatch_response
# ---------------------------------------------------------------------------


class TestDispatchResponse:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[InboundMessage, OutboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append((original_msg, outbound))

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", CapturingAdapter()),
        )
        msg = make_inbound_message(platform="telegram", bot_id="main")
        response = Response(content="pong")
        await hub.dispatch_response(msg, response)
        assert len(sent) == 1
        # dispatch_response converts Response → OutboundMessage; text is preserved
        assert "pong" in str(sent[0][1].content)

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))

    async def test_updates_last_processed_at_on_success(self) -> None:
        """dispatch_response sets _last_processed_at on successful send."""
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", DummyAdapter()),
        )
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        await hub.dispatch_response(msg, Response(content="ok"))
        assert hub._last_processed_at is not None

    async def test_no_update_last_processed_at_on_missing_adapter(self) -> None:
        """dispatch_response does NOT update _last_processed_at on KeyError."""
        hub = Hub()
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))
        assert hub._last_processed_at is None


# ---------------------------------------------------------------------------
# #217 — dispatch_attachment
# ---------------------------------------------------------------------------


class TestDispatchAttachment:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[OutboundAttachment, InboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                sent.append((attachment, inbound))

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", CapturingAdapter()),
        )
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert len(sent) == 1
        assert sent[0][0] is attachment

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        with pytest.raises(KeyError):
            await hub.dispatch_attachment(msg, attachment)

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                pass

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", DummyAdapter()),
        )
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# RED — #138: OutboundMessage dispatch (Slice V2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_response_accepts_outbound_message() -> None:
    """hub.dispatch_response() must accept an OutboundMessage and forward it
    to the adapter's send() method unchanged (issue #138, Slice V2)."""
    hub = Hub()
    received: list[OutboundMessage] = []

    class MockAdapterV2:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(
        Platform.TELEGRAM,
        "main",
        cast("ChannelAdapter", MockAdapterV2()),
    )
    msg = make_inbound_message(platform="telegram", bot_id="main")
    outbound = OutboundMessage.from_text("hi")

    await hub.dispatch_response(msg, outbound)

    assert len(received) == 1
    assert received[0].content == ["hi"]


@pytest.mark.asyncio
async def test_dispatch_response_accepts_legacy_response() -> None:
    """hub.dispatch_response() must still accept a plain Response for backward
    compatibility — no call-site changes required at pool.py (issue #138, U5)."""
    hub = Hub()
    received: list[OutboundMessage] = []

    class LegacyCapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(
        Platform.TELEGRAM,
        "main",
        cast("ChannelAdapter", LegacyCapturingAdapter()),
    )
    msg = make_inbound_message(platform="telegram", bot_id="main")

    await hub.dispatch_response(msg, Response(content="hi"))

    assert len(received) == 1
    result = received[0]
    assert "hi" in str(result.content)
