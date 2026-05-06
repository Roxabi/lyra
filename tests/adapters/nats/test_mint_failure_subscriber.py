"""Tests for MintFailureSubscriber — hub-side NATS alert forwarding."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.gh.models import MintFailureEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides: Any) -> MintFailureEvent:
    base: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "tst-trace-001",
        "issued_at": datetime(2026, 5, 4, tzinfo=timezone.utc),
        "machine": "roxabituwer",
        "reason": "github_api_401",
        "http_status": 401,
        "retries": 2,
    }
    base.update(overrides)
    return MintFailureEvent.model_validate(base)


def _make_nats_msg(
    data: bytes, subject: str = "lyra.gh.mint_failure.roxabituwer"
) -> MagicMock:
    msg = MagicMock()
    msg.data = data
    msg.subject = subject
    return msg


# ---------------------------------------------------------------------------
# test_subscribes_to_correct_subject_on_start
# ---------------------------------------------------------------------------


async def test_subscribes_to_correct_subject_on_start() -> None:
    """start() subscribes to lyra.gh.mint_failure.> wildcard."""
    from lyra.adapters.nats.mint_failure_subscriber import (
        _SUBSCRIBE_SUBJECT,
        MintFailureSubscriber,
    )

    nc = AsyncMock()
    subscriber = MintFailureSubscriber(
        nc,
        ops_telegram_bot_id="main",
        ops_telegram_chat_id=123456,
    )
    await subscriber.start()

    nc.subscribe.assert_called_once()
    call_args = nc.subscribe.call_args
    assert call_args[0][0] == _SUBSCRIBE_SUBJECT
    assert call_args[0][0] == "lyra.gh.mint_failure.>"


# ---------------------------------------------------------------------------
# test_handle_publishes_telegram_outbound
# ---------------------------------------------------------------------------


async def test_handle_publishes_telegram_outbound() -> None:
    """Valid MintFailureEvent -> nc.publish called with correct subject and payload."""
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    nc = AsyncMock()
    subscriber = MintFailureSubscriber(
        nc,
        ops_telegram_bot_id="main",
        ops_telegram_chat_id=999,
    )

    event = _make_event(
        machine="M1", reason="github_api_404", http_status=404, retries=3
    )
    raw = event.model_dump_json().encode()
    msg = _make_nats_msg(raw, subject="lyra.gh.mint_failure.M1")

    await subscriber._handle(msg)

    nc.publish.assert_called_once()
    publish_subject, publish_payload = nc.publish.call_args[0]

    # Subject must target the ops telegram bot
    assert publish_subject == "lyra.outbound.telegram.main"

    # Payload is a JSON "send" envelope
    envelope = json.loads(publish_payload)
    assert envelope["type"] == "send"
    assert "outbound" in envelope
    assert "original_msg" in envelope

    # The outbound message content contains expected alert fields
    outbound_data = envelope["outbound"]
    content_parts = outbound_data["content"]
    assert isinstance(content_parts, list)
    full_text = " ".join(str(p) for p in content_parts)
    assert "M1" in full_text
    assert "github_api_404" in full_text
    assert "404" in full_text
    assert "3" in full_text  # retries

    # The original_msg targets the ops chat
    original_msg_data = envelope["original_msg"]
    assert original_msg_data["platform_meta"]["chat_id"] == 999


# ---------------------------------------------------------------------------
# test_handle_logs_and_swallows_bad_payload
# ---------------------------------------------------------------------------


async def test_handle_logs_and_swallows_bad_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Garbage payload must not raise — subscriber logs warning and keeps running."""
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    nc = AsyncMock()
    subscriber = MintFailureSubscriber(
        nc,
        ops_telegram_bot_id="main",
        ops_telegram_chat_id=123,
    )

    bad_msg = _make_nats_msg(b"not valid json at all !!!!")

    logger_name = "lyra.adapters.nats.mint_failure_subscriber"
    with caplog.at_level(logging.WARNING, logger=logger_name):
        await subscriber._handle(bad_msg)

    # Must not publish anything
    nc.publish.assert_not_called()

    # Must have logged a warning
    assert any("failed to parse" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# test_stop_unsubscribes
# ---------------------------------------------------------------------------


async def test_stop_unsubscribes() -> None:
    """start() then stop() must call unsubscribe() on the subscription."""
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    nc = AsyncMock()
    fake_sub = AsyncMock()
    nc.subscribe.return_value = fake_sub

    subscriber = MintFailureSubscriber(
        nc,
        ops_telegram_bot_id="main",
        ops_telegram_chat_id=123456,
    )
    await subscriber.start()
    await subscriber.stop()

    fake_sub.unsubscribe.assert_called_once()
    # Second stop must be a no-op (sub is None after first stop)
    await subscriber.stop()
    fake_sub.unsubscribe.assert_called_once()


# ---------------------------------------------------------------------------
# test_stop_without_start_is_noop
# ---------------------------------------------------------------------------


async def test_stop_without_start_is_noop() -> None:
    """stop() before start() does not raise."""
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    nc = AsyncMock()
    subscriber = MintFailureSubscriber(
        nc,
        ops_telegram_bot_id="main",
        ops_telegram_chat_id=123,
    )
    # Should not raise
    await subscriber.stop()
