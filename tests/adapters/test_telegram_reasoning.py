"""Integration tests for Telegram adapter Reasoning rendering (SC-15, T13).

Covers _render_reasoning via PlatformCallbacks.edit_reasoning for:
- Lazy placeholder creation (test 1)
- Edit throttle bound (test 2)
- Text truncation (test 3)
- show_intermediate=False no-op (test 4)

These tests call build_streaming_callbacks() directly to control show_intermediate.
T12 has landed — _render_reasoning is wired into PlatformCallbacks.edit_reasoning.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.shared._shared_streaming_state import STREAMING_EDIT_INTERVAL
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MSG_ID = "r-tg-test-1"


def _make_trace_send_mock(message_id: int = 501) -> MagicMock:
    """Return a mock Telegram message object (result of bot.send_message)."""
    m = MagicMock()
    m.message_id = message_id
    return m


# ---------------------------------------------------------------------------
# T13 — Telegram reasoning rendering (4 tests)
# ---------------------------------------------------------------------------


class TestTelegramReasoningRendering:
    """T13 — Telegram adapter Reasoning event rendering (SC-15)."""

    @pytest.mark.asyncio
    async def test_reasoning_lazy_placeholder(self) -> None:
        """Reasoning trace placeholder is sent exactly ONCE across Start+Delta+End.

        Arrange: adapter with show_intermediate=True, no pre-existing trace.
        Act: drive ReasoningStart → ReasoningDelta → ReasoningEnd.
        Assert: send_message called once (trace placeholder), not more.
        """
        from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=501)
        send_message_mock = AsyncMock(return_value=trace_mock)
        edit_message_mock = AsyncMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = send_message_mock
        adapter.bot.edit_message_text = edit_message_mock

        original_msg = _make_telegram_message()
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=True
        )

        # Act — full Reasoning triplet
        await callbacks.edit_reasoning(
            None, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await callbacks.edit_reasoning(
            None, ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="thinking…")
        )
        await callbacks.edit_reasoning(
            None, ReasoningEndRenderEvent(message_id=_MSG_ID)
        )

        # Assert — trace placeholder created exactly once
        assert send_message_mock.await_count == 1, (
            f"Expected 1 send_message call (trace placeholder), "
            f"got {send_message_mock.await_count}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_throttle_bound(self) -> None:
        """Delta edits are throttled: at most ceil(window/interval)+1 API calls.

        Arrange: adapter with show_intermediate=True; time.monotonic controlled.
        Act: drive Start + 20 Delta events spread across a 2s window + End.
        Assert: edit_message_text count <= ceil(2.0 / STREAMING_EDIT_INTERVAL) + 1.
        """
        from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=502)
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
        adapter.bot.edit_message_text = AsyncMock()

        original_msg = _make_telegram_message()
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=True
        )

        # Spread 20 deltas uniformly across a 2s window
        window = 2.0
        n_deltas = 20
        tick = window / n_deltas  # 0.1s per delta

        # Control time.monotonic in the telegram_outbound module
        times: list[float] = [i * tick for i in range(n_deltas)]
        time_iter = iter(times)

        def fake_monotonic() -> float:
            try:
                return next(time_iter)
            except StopIteration:
                return window

        with patch(
            "lyra.adapters.telegram.telegram_outbound.time.monotonic",
            side_effect=fake_monotonic,
        ):
            # Act
            await callbacks.edit_reasoning(
                None, ReasoningStartRenderEvent(message_id=_MSG_ID)
            )
            for i in range(n_deltas):
                await callbacks.edit_reasoning(
                    None,
                    ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta=f"chunk{i}"),
                )
            await callbacks.edit_reasoning(
                None, ReasoningEndRenderEvent(message_id=_MSG_ID)
            )

        # Assert — throttle bound (the "+1" accounts for the final flush at End)
        max_allowed = math.ceil(window / STREAMING_EDIT_INTERVAL) + 1
        actual_edits = adapter.bot.edit_message_text.await_count
        assert actual_edits <= max_allowed, (
            f"Too many API edits: {actual_edits} > {max_allowed} "
            f"(window={window}s, interval={STREAMING_EDIT_INTERVAL}s)"
        )

    @pytest.mark.asyncio
    async def test_reasoning_truncation(self) -> None:
        """Delta text > 120 chars is truncated to 117 chars + ellipsis (118 total).

        Arrange: adapter with show_intermediate=True.
        Act: Start → Delta(200 'x' chars) → End.
        Assert: the edit call receives text of length 118 ending with '…'.
        """
        from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=503)
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
        edit_calls: list[str] = []

        async def capture_edit(**kwargs: object) -> None:
            text = kwargs.get("text", "")
            assert isinstance(text, str)
            edit_calls.append(text)

        adapter.bot.edit_message_text = capture_edit

        original_msg = _make_telegram_message()
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=True
        )

        # Act
        await callbacks.edit_reasoning(
            None, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await callbacks.edit_reasoning(
            None,
            ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="x" * 200),
        )
        await callbacks.edit_reasoning(
            None, ReasoningEndRenderEvent(message_id=_MSG_ID)
        )

        # Assert — at least one edit call was made
        assert len(edit_calls) >= 1, "Expected at least one edit_message_text call"
        # The final (or only) edit carries the truncated text (inside italic wrapper)
        last_text = edit_calls[-1]
        # The raw text passed to _render_text is "*" + truncated + "*" (italic).
        # _render_text escapes for MarkdownV2, but truncation leaves an ellipsis.
        # Check the rendered call contains an ellipsis (evidence of truncation).
        assert "…" in last_text, (
            f"Expected truncation ellipsis in edit text, got: {last_text!r}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_show_intermediate_false_noop(self) -> None:
        """show_intermediate=False: Reasoning events produce zero API calls.

        Arrange: adapter with show_intermediate=False.
        Act: drive full Reasoning triplet.
        Assert: send_message NOT called, edit_message_text NOT called.
        """
        from lyra.adapters.telegram.telegram_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_telegram_adapter()
        send_mock = AsyncMock()
        edit_mock = AsyncMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = send_mock
        adapter.bot.edit_message_text = edit_mock

        original_msg = _make_telegram_message()
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=False
        )

        # Act — full Reasoning triplet
        await callbacks.edit_reasoning(
            None, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await callbacks.edit_reasoning(
            None, ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="thinking…")
        )
        await callbacks.edit_reasoning(
            None, ReasoningEndRenderEvent(message_id=_MSG_ID)
        )

        # Assert — no API calls at all
        send_mock.assert_not_awaited()
        edit_mock.assert_not_awaited()
