"""Tests for _handle_typing_tail delegation (#1377 Blocker 1).

After Blocker 1, _handle_typing_tail delegates entirely to emitter._start_typing /
_cancel_typing. The pub/sub-vs-legacy gating lives in those methods (tested in
test_emitter_typing.py). This file owns the delegation contract only.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.messaging.message import OutboundMessage
from lyra.outbound._placeholder_lifecycle import _handle_typing_tail


def _make_emitter(*, intermediate: bool = False) -> MagicMock:
    """Build a MagicMock standing in for OutboundEmitter with required attrs."""
    emitter = MagicMock()
    outbound = OutboundMessage.from_text("hi")
    outbound.intermediate = intermediate
    emitter._outbound = outbound
    emitter._start_typing = AsyncMock()
    emitter._cancel_typing = AsyncMock()
    return emitter


class TestHandleTypingTailDelegation:
    @pytest.mark.asyncio
    async def test_intermediate_delegates_to_start_typing(self):
        """intermediate=True → _start_typing called, _cancel_typing not called."""
        emitter = _make_emitter(intermediate=True)
        await _handle_typing_tail(emitter)
        emitter._start_typing.assert_awaited_once()
        emitter._cancel_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_final_delegates_to_cancel_typing(self):
        """intermediate=False → _cancel_typing called, _start_typing not called."""
        emitter = _make_emitter(intermediate=False)
        await _handle_typing_tail(emitter)
        emitter._cancel_typing.assert_awaited_once()
        emitter._start_typing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outbound_none_delegates_to_cancel_typing(self):
        """_outbound is None → treated as non-intermediate → _cancel_typing."""
        emitter = MagicMock()
        emitter._outbound = None
        emitter._start_typing = AsyncMock()
        emitter._cancel_typing = AsyncMock()
        await _handle_typing_tail(emitter)
        emitter._cancel_typing.assert_awaited_once()
        emitter._start_typing.assert_not_awaited()
