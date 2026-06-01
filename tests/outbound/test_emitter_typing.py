"""Tests for OutboundEmitter._start_typing and _cancel_typing pub/sub path (#1377)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.messaging.message import OutboundMessage
from lyra.outbound.emitter import OutboundEmitter
from lyra.transport.work_scope import WorkScope


def _make_formatter() -> MagicMock:
    """Build a mock OutboundFormatter with minimal defaults."""
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.get_msg = MagicMock(side_effect=lambda _, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    return fmt


def _make_emitter(
    *,
    typing_publisher=None,
    work_scope=None,
    typing=None,
    typing_scope_id=None,
) -> OutboundEmitter:
    """Build an OutboundEmitter with injectable typing pub/sub or legacy path."""
    fmt = _make_formatter()
    outbound = OutboundMessage.from_text("hi")
    emitter = OutboundEmitter(
        fmt,
        outbound,
        typing=typing,
        typing_scope_id=typing_scope_id,
    )
    emitter.typing_publisher = typing_publisher
    emitter._work_scope = work_scope
    return emitter


class TestStartTyping:
    @pytest.mark.asyncio
    async def test_pub_sub_path_awaited(self):
        """Enabled + publisher + scope → publish_started."""
        scope = WorkScope(
            platform="telegram", bot_id="main", scope_id=1, trace_id="abc"
        )
        tp = AsyncMock()
        emitter = _make_emitter(typing_publisher=tp, work_scope=scope)

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._start_typing()

        tp.publish_started.assert_awaited_once_with(scope)
        tp.publish_ended.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_work_scope_none(self):
        """Enabled + publisher + scope=None → legacy fallback."""
        tp = AsyncMock()
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=tp,
            work_scope=None,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._start_typing()

        tp.publish_started.assert_not_awaited()
        typing.start_typing.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_typing_publisher_none(self):
        """Enabled + publisher=None + typing present → legacy fallback."""
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=None,
            work_scope=None,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._start_typing()

        typing.start_typing.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_typing_disabled(self):
        """Disabled + publisher + typing present → legacy fallback."""
        scope = WorkScope(
            platform="telegram", bot_id="main", scope_id=1, trace_id="abc"
        )
        tp = AsyncMock()
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=tp,
            work_scope=scope,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=False):
            await emitter._start_typing()

        tp.publish_started.assert_not_awaited()
        typing.start_typing.assert_awaited_once_with(42)


class TestCancelTyping:
    @pytest.mark.asyncio
    async def test_pub_sub_path_awaited(self):
        """Enabled + publisher + scope → publish_ended."""
        scope = WorkScope(
            platform="telegram", bot_id="main", scope_id=1, trace_id="abc"
        )
        tp = AsyncMock()
        emitter = _make_emitter(typing_publisher=tp, work_scope=scope)

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._cancel_typing()

        tp.publish_ended.assert_awaited_once_with(scope)
        tp.publish_started.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_work_scope_none(self):
        """Enabled + publisher + scope=None → legacy fallback."""
        tp = AsyncMock()
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=tp,
            work_scope=None,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._cancel_typing()

        tp.publish_ended.assert_not_awaited()
        typing.cancel_typing.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_typing_publisher_none(self):
        """Enabled + publisher=None + typing present → legacy fallback."""
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=None,
            work_scope=None,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=True):
            await emitter._cancel_typing()

        typing.cancel_typing.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_legacy_fallback_when_typing_disabled(self):
        """Disabled + publisher + typing present → legacy fallback."""
        scope = WorkScope(
            platform="telegram", bot_id="main", scope_id=1, trace_id="abc"
        )
        tp = AsyncMock()
        typing = AsyncMock()
        emitter = _make_emitter(
            typing_publisher=tp,
            work_scope=scope,
            typing=typing,
            typing_scope_id=42,
        )

        with patch("lyra.outbound.emitter.is_typing_enabled", return_value=False):
            await emitter._cancel_typing()

        tp.publish_ended.assert_not_awaited()
        typing.cancel_typing.assert_awaited_once_with(42)
