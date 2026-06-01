"""Tests for _handle_typing_tail pub/sub path (#1377)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.messaging.message import OutboundMessage
from lyra.outbound._placeholder_lifecycle import _handle_typing_tail
from lyra.transport.work_scope import WorkScope


def _make_scope() -> WorkScope:
    return WorkScope(platform="telegram", bot_id="main", scope_id=1, trace_id="abc")


def _make_emitter(
    *,
    intermediate: bool = False,
    typing_publisher=None,
    work_scope=None,
) -> MagicMock:
    """Build a MagicMock standing in for OutboundEmitter with required attrs."""
    emitter = MagicMock()
    outbound = OutboundMessage.from_text("hi")
    outbound.intermediate = intermediate
    emitter._outbound = outbound
    emitter.typing_publisher = typing_publisher
    emitter._work_scope = work_scope
    emitter._start_typing = AsyncMock()
    emitter._cancel_typing = AsyncMock()
    return emitter


class TestHandleTypingTailPubSub:
    @pytest.mark.asyncio
    async def test_intermediate_with_typing_publisher_calls_publish_started(self):
        """intermediate=True + typing_publisher set → publish_started."""
        scope = _make_scope()
        tp = AsyncMock()
        emitter = _make_emitter(
            intermediate=True, typing_publisher=tp, work_scope=scope
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=True,
        ):
            await _handle_typing_tail(emitter)

        tp.publish_started.assert_awaited_once_with(scope)
        tp.publish_ended.assert_not_awaited()
        emitter._start_typing.assert_not_awaited()
        emitter._cancel_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_final_with_typing_publisher_calls_publish_ended(self):
        """intermediate=False + typing_publisher set → publish_ended."""
        scope = _make_scope()
        tp = AsyncMock()
        emitter = _make_emitter(
            intermediate=False, typing_publisher=tp, work_scope=scope
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=True,
        ):
            await _handle_typing_tail(emitter)

        tp.publish_ended.assert_awaited_once_with(scope)
        tp.publish_started.assert_not_awaited()
        emitter._start_typing.assert_not_awaited()
        emitter._cancel_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_intermediate_no_typing_publisher_falls_back_to_start_typing(self):
        """intermediate=True + typing_publisher=None → _start_typing fallback."""
        emitter = _make_emitter(
            intermediate=True, typing_publisher=None, work_scope=None
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=True,
        ):
            await _handle_typing_tail(emitter)

        emitter._start_typing.assert_awaited_once()
        emitter._cancel_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_final_no_typing_publisher_falls_back_to_cancel_typing(self):
        """intermediate=False + typing_publisher=None → _cancel_typing fallback."""
        emitter = _make_emitter(
            intermediate=False, typing_publisher=None, work_scope=None
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=True,
        ):
            await _handle_typing_tail(emitter)

        emitter._cancel_typing.assert_awaited_once()
        emitter._start_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_typing_disabled_falls_back_to_legacy_methods(self):
        """is_typing_enabled=False always falls back to legacy _start/_cancel."""
        scope = _make_scope()
        tp = AsyncMock()
        emitter = _make_emitter(
            intermediate=True, typing_publisher=tp, work_scope=scope
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=False,
        ):
            await _handle_typing_tail(emitter)

        emitter._start_typing.assert_awaited_once()
        tp.publish_started.assert_not_awaited()
        tp.publish_ended.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_typing_disabled_final_falls_back_to_cancel_typing(self):
        """is_typing_enabled=False + intermediate=False falls back to _cancel_typing."""
        emitter = _make_emitter(
            intermediate=False, typing_publisher=None, work_scope=None
        )

        with patch(
            "lyra.outbound._placeholder_lifecycle.is_typing_enabled",
            return_value=False,
        ):
            await _handle_typing_tail(emitter)

        emitter._cancel_typing.assert_awaited_once()
        emitter._start_typing.assert_not_awaited()
