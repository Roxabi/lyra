"""Shared test helpers for core tests."""

from __future__ import annotations

from datetime import datetime, timezone

from lyra.core.message import (
    InboundMessage,
)
from lyra.core.trust import TrustLevel


def make_inbound_message(
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    scope_id: str | None = None,
    platform_meta: dict | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for hub tests."""
    if platform == "telegram":
        _scope = scope_id if scope_id is not None else "chat:42"
        _meta = (
            platform_meta
            if platform_meta is not None
            else {
                "chat_id": 42,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            }
        )
    elif platform == "discord":
        _scope = scope_id if scope_id is not None else "channel:333"
        _meta = (
            platform_meta
            if platform_meta is not None
            else {
                "guild_id": 111,
                "channel_id": 333,
                "message_id": 555,
                "thread_id": None,
                "channel_type": "text",
            }
        )
    else:
        _scope = scope_id if scope_id is not None else f"{platform}:default"
        _meta = platform_meta or {}
    return InboundMessage(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        scope_id=_scope,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=_meta,
        trust_level=TrustLevel.TRUSTED,
    )
