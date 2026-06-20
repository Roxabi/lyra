"""Tests for adapter glue kit shared modules (#1931)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.adapters.shared.inbound import (
    cancel_typing_for_inbound,
    cancel_typing_shim,
    get_inbound_pipeline_kit,
    reset_inbound_pipeline_kit,
    run_inbound_guarded,
    start_typing_shim,
)
from factory.adapters.shared.inbound.typing_shim import _typing_work_scope
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import DiscordMeta, InboundMessage, TelegramMeta
from factory.inbound.attachment_ingest import AttachmentIngestError


def _minimal_inbound(**kwargs: Any) -> InboundMessage:
    return InboundMessage(
        id=kwargs.get("id", "m1"),
        platform=kwargs.get("platform", "telegram"),
        bot_id=kwargs.get("bot_id", "main"),
        scope_id=kwargs.get("scope_id", "chat:1"),
        user_id=kwargs.get("user_id", "u1"),
        user_name=kwargs.get("user_name", "user"),
        is_mention=kwargs.get("is_mention", False),
        text=kwargs.get("text", "hi"),
        text_raw=kwargs.get("text_raw", "hi"),
        trust_level=kwargs.get("trust_level", TrustLevel.PUBLIC),
        platform_meta=kwargs.get(
            "platform_meta", TelegramMeta(chat_id=1, message_id=1)
        ),
    )


@pytest.fixture(autouse=True)
def _reset_pipeline_kit() -> Iterator[None]:
    reset_inbound_pipeline_kit()
    yield
    reset_inbound_pipeline_kit()


def test_pipeline_kit_reset_clears_parser_cache() -> None:
    kit = get_inbound_pipeline_kit()
    kit.parser_cache[1] = object()
    kit.reset()
    assert kit.parser_cache == {}


def test_start_typing_shim_legacy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    typing_manager = MagicMock()
    factory_builder = MagicMock(return_value=lambda: None)
    with patch(
        "factory.adapters.shared.inbound.typing_shim.is_typing_enabled",
        return_value=False,
    ):
        start_typing_shim(
            platform="telegram",
            bot_id="main",
            scope_id=42,
            typing_manager=typing_manager,
            factory_builder=factory_builder,
            typing_publisher=None,
        )
    typing_manager.start.assert_called_once_with(42, factory_builder.return_value)


@pytest.mark.parametrize("trace_id", [None, ""])
def test_typing_work_scope_trace_id_falls_back_to_uuid4(
    trace_id: str | None,
) -> None:
    with patch(
        "factory.adapters.shared.inbound.typing_shim.TraceContext.get_trace_id",
        return_value=trace_id,
    ):
        with patch(
            "factory.adapters.shared.inbound.typing_shim.uuid4"
        ) as mock_uuid:
            mock_uuid.return_value.hex = "deadbeef1234567890abcdef12345678"
            scope = _typing_work_scope("telegram", "main", 42)

    assert scope.platform == "telegram"
    assert scope.bot_id == "main"
    assert scope.scope_id == 42
    assert scope.trace_id == "deadbeef1234567890abcdef12345678"


def test_typing_work_scope_trace_id_from_context() -> None:
    with patch(
        "factory.adapters.shared.inbound.typing_shim.TraceContext.get_trace_id",
        return_value="trace-from-context",
    ):
        scope = _typing_work_scope("discord", "main", 7)

    assert scope.trace_id == "trace-from-context"


def test_cancel_typing_for_discord_meta() -> None:
    adapter = MagicMock()
    inbound = _minimal_inbound(
        platform="discord",
        scope_id="thread:9",
        platform_meta=DiscordMeta(guild_id=1, channel_id=2, message_id=3, thread_id=9),
    )
    cancel_typing_for_inbound(adapter, inbound)
    adapter._cancel_typing.assert_called_once_with(9)


def test_cancel_typing_for_telegram_meta() -> None:
    adapter = MagicMock()
    inbound = _minimal_inbound(
        scope_id="chat:55",
        platform_meta=TelegramMeta(chat_id=55, message_id=1),
    )
    cancel_typing_for_inbound(adapter, inbound)
    adapter._cancel_typing.assert_called_once_with(55)


@pytest.mark.asyncio
async def test_run_inbound_guarded_swallows_unhandled_errors() -> None:
    pipeline = AsyncMock()
    pipeline.run.side_effect = RuntimeError("boom")

    await run_inbound_guarded(
        pipeline=pipeline,
        raw_message=object(),
        inbound_ctx=MagicMock(),
        parser=MagicMock(),
        log_context="test",
        on_attachment_ingest_error=AsyncMock(),
        send_backpressure=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_run_inbound_guarded_handles_attachment_ingest_error() -> None:
    pipeline = AsyncMock()
    pipeline.run.side_effect = AttachmentIngestError(
        user_message="nope", reason="oversize"
    )
    on_error = AsyncMock()

    await run_inbound_guarded(
        pipeline=pipeline,
        raw_message=object(),
        inbound_ctx=MagicMock(),
        parser=MagicMock(),
        log_context="test",
        on_attachment_ingest_error=on_error,
        send_backpressure=AsyncMock(),
    )

    on_error.assert_awaited_once()
    exc = on_error.call_args.args[0]
    assert isinstance(exc, AttachmentIngestError)
    assert exc.user_message == "nope"


def test_cancel_typing_shim_pubsub_noop_when_enabled_without_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typing_manager = MagicMock()
    with patch(
        "factory.adapters.shared.inbound.typing_shim.is_typing_enabled",
        return_value=True,
    ):
        cancel_typing_shim(
            platform="discord",
            bot_id="main",
            scope_id=1,
            typing_manager=typing_manager,
            typing_publisher=None,
        )
    typing_manager.cancel.assert_not_called()
