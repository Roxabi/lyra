"""Tests for DeadLetterConsumer — DLQ user notification."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dlq_msg(data: bytes, subject: str = "lyra.outbound.dlq") -> MagicMock:
    msg = MagicMock()
    msg.data = data
    msg.subject = subject
    return msg


def _make_outbound_envelope(
    platform: str = "telegram", bot_id: str = "main", **overrides: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "send",
        "platform": platform,
        "bot_id": bot_id,
        "outbound": {
            "content": ["hello"],
            "metadata": {},
            "buttons": [],
            "intermediate": False,
        },
        "original_msg": {
            "id": "msg-001",
            "platform": platform,
            "bot_id": bot_id,
            "scope_id": "scope-001",
            "user_id": "user-001",
            "user_name": "Test User",
            "is_mention": True,
            "text": "hello",
            "text_raw": "hello",
            "trust_level": "public",
            "platform_meta": {"chat_id": 123},
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# test_subscribe_dlq
# ---------------------------------------------------------------------------


async def test_subscribe_dlq() -> None:
    """start() subscribes to the DLQ subject and stores the subscription."""
    from lyra.adapters.nats.dead_letter_consumer import DeadLetterConsumer

    js = AsyncMock()
    adapter = MagicMock()
    from lyra.core.messaging.message import Platform

    consumer = DeadLetterConsumer(
        js,
        Platform.TELEGRAM,
        "main",
        adapter,
        subject="lyra.outbound.dlq",
    )
    await consumer.start()

    js.subscribe.assert_called_once()
    assert consumer._sub is not None


# ---------------------------------------------------------------------------
# test_notify_telegram
# ---------------------------------------------------------------------------


async def test_notify_telegram() -> None:
    """DLQ message for Telegram triggers adapter.send_failure_notification."""
    from lyra.adapters.nats.dead_letter_consumer import DeadLetterConsumer

    js = AsyncMock()
    adapter = MagicMock()
    adapter.send_failure_notification = AsyncMock()
    from lyra.core.messaging.message import Platform

    consumer = DeadLetterConsumer(
        js,
        Platform.TELEGRAM,
        "main",
        adapter,
        subject="lyra.outbound.dlq",
    )

    envelope = _make_outbound_envelope(platform="telegram", bot_id="main")
    raw = json.dumps(envelope).encode()
    msg = _make_dlq_msg(raw)

    await consumer._notify_user(msg)

    assert adapter.send_failure_notification.called


# ---------------------------------------------------------------------------
# test_notify_discord
# ---------------------------------------------------------------------------


async def test_notify_discord() -> None:
    """DLQ message for Discord triggers adapter.send_failure_notification."""
    from lyra.adapters.nats.dead_letter_consumer import DeadLetterConsumer

    js = AsyncMock()
    adapter = MagicMock()
    adapter.send_failure_notification = AsyncMock()
    from lyra.core.messaging.message import Platform

    consumer = DeadLetterConsumer(
        js,
        Platform.TELEGRAM,
        "main",
        adapter,
        subject="lyra.outbound.dlq",
    )

    envelope = _make_outbound_envelope(platform="discord", bot_id="main")
    raw = json.dumps(envelope).encode()
    msg = _make_dlq_msg(raw)

    await consumer._notify_user(msg)

    assert adapter.send_failure_notification.called


# ---------------------------------------------------------------------------
# test_stop_unsubscribes
# ---------------------------------------------------------------------------


async def test_stop_unsubscribes() -> None:
    """start() then stop() must unsubscribe the DLQ subscription."""
    from lyra.adapters.nats.dead_letter_consumer import DeadLetterConsumer

    js = AsyncMock()
    fake_sub = AsyncMock()
    js.subscribe.return_value = fake_sub
    adapter = MagicMock()

    from lyra.core.messaging.message import Platform

    consumer = DeadLetterConsumer(
        js,
        Platform.TELEGRAM,
        "main",
        adapter,
        subject="lyra.outbound.dlq",
    )
    await consumer.start()
    await consumer.stop()

    fake_sub.unsubscribe.assert_called_once()


# ---------------------------------------------------------------------------
# test_stop_without_start_is_noop
# ---------------------------------------------------------------------------


async def test_stop_without_start_is_noop() -> None:
    """stop() before start() does not raise."""
    from lyra.adapters.nats.dead_letter_consumer import DeadLetterConsumer

    js = AsyncMock()
    adapter = MagicMock()
    from lyra.core.messaging.message import Platform

    consumer = DeadLetterConsumer(
        js,
        Platform.TELEGRAM,
        "main",
        adapter,
        subject="lyra.outbound.dlq",
    )
    # Should not raise
    await consumer.stop()
