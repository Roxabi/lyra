"""Routing context — basic dataclass, inbound message, and outbound propagation.

Covers:
- RoutingContext creation and immutability
- InboundMessage carries routing
- InboundAudio carries routing
- Response → OutboundMessage propagation
- OutboundMessage routing field
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lyra.core.message import (
    InboundAudio,
    OutboundMessage,
    Response,
    RoutingContext,
)
from lyra.core.trust import TrustLevel

from .conftest import _RC_DC, _RC_TG, make_routing_inbound

# ---------------------------------------------------------------------------
# RoutingContext dataclass
# ---------------------------------------------------------------------------


class TestRoutingContext:
    def test_importable_from_core(self) -> None:
        from lyra.core import RoutingContext as RC  # noqa: F401

        assert RC is RoutingContext

    def test_creation(self) -> None:
        rc = RoutingContext(
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
        )
        assert rc.platform == "telegram"
        assert rc.bot_id == "main"
        assert rc.scope_id == "chat:123"
        assert rc.thread_id is None
        assert rc.reply_to_message_id is None
        assert rc.platform_meta == {}

    def test_frozen(self) -> None:
        rc = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
        with pytest.raises(AttributeError):
            rc.platform = "discord"  # type: ignore[misc]

    def test_with_all_fields(self) -> None:
        meta = {"chat_id": 123, "topic_id": 456}
        rc = RoutingContext(
            platform="telegram",
            bot_id="main",
            scope_id="chat:123:topic:456",
            thread_id="456",
            reply_to_message_id="789",
            platform_meta=meta,
        )
        assert rc.thread_id == "456"
        assert rc.reply_to_message_id == "789"
        assert rc.platform_meta is meta


# ---------------------------------------------------------------------------
# InboundMessage carries routing
# ---------------------------------------------------------------------------


class TestInboundMessageRouting:
    def test_default_none(self) -> None:
        msg = make_routing_inbound()
        assert msg.routing is None

    def test_with_routing(self) -> None:
        msg = make_routing_inbound(routing=_RC_TG)
        assert msg.routing is _RC_TG


# ---------------------------------------------------------------------------
# InboundAudio carries routing
# ---------------------------------------------------------------------------


class TestInboundAudioRouting:
    def test_default_none(self) -> None:
        audio = InboundAudio(
            id="a-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
            user_id="tg:user:42",
            audio_bytes=b"fake",
            mime_type="audio/ogg",
            duration_ms=None,
            file_id=None,
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )
        assert audio.routing is None

    def test_with_routing(self) -> None:
        audio = InboundAudio(
            id="a-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
            user_id="tg:user:42",
            audio_bytes=b"fake",
            mime_type="audio/ogg",
            duration_ms=None,
            file_id=None,
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
            routing=_RC_TG,
        )
        assert audio.routing is _RC_TG


# ---------------------------------------------------------------------------
# Response → OutboundMessage propagation
# ---------------------------------------------------------------------------


class TestResponseRoutingPropagation:
    def test_response_routing_default_none(self) -> None:
        r = Response(content="hi")
        assert r.routing is None

    def test_to_outbound_propagates_routing(self) -> None:
        r = Response(content="hi", routing=_RC_TG)
        outbound = r.to_outbound()
        assert outbound.routing is _RC_TG

    def test_to_outbound_none_routing(self) -> None:
        r = Response(content="hi")
        outbound = r.to_outbound()
        assert outbound.routing is None


# ---------------------------------------------------------------------------
# OutboundMessage routing field
# ---------------------------------------------------------------------------


class TestOutboundMessageRouting:
    def test_default_none(self) -> None:
        om = OutboundMessage.from_text("hi")
        assert om.routing is None

    def test_assignable(self) -> None:
        om = OutboundMessage.from_text("hi")
        om.routing = _RC_DC
        assert om.routing is _RC_DC
