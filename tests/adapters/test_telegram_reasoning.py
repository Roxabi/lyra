"""Integration tests for Telegram adapter Reasoning rendering (SC-15, T13).

Covers TelegramFormatter.edit_reasoning for:
- Lazy placeholder creation (back-to-back blocks share one placeholder)
- Edit throttle bound
- Text truncation

The show_intermediate=False gate lives upstream on StreamProcessor (SC-6) — no
Reasoning* events reach this callback when disabled, so adapter-level coverage
is not needed here (see test_stream_processor.py::TestReasoning).

Migrated in S7 (#1501): build_streaming_callbacks replaced with TelegramFormatter
direct construction.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL
from tests.adapters.conftest import _make_telegram_adapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MSG_ID = "r-tg-test-1"


def _make_trace_send_mock(message_id: int = 501) -> MagicMock:
    """Return a mock Telegram message object (result of bot.send_message)."""
    m = MagicMock()
    m.message_id = message_id
    return m


def _make_formatter(adapter, *, reply_to: int | None = None):
    """Build a TelegramFormatter for tests."""
    from lyra.adapters.telegram.telegram_formatter import TelegramFormatter

    return TelegramFormatter(
        adapter,
        chat_id=123,
        get_msg=lambda k, fb: fb,
        placeholder_text="…",
        reply_to=reply_to,
    )


# ---------------------------------------------------------------------------
# T13 — Telegram reasoning rendering (4 tests)
# ---------------------------------------------------------------------------


class TestTelegramReasoningRendering:
    """T13 — Telegram adapter Reasoning event rendering (SC-15)."""

    @pytest.mark.asyncio
    async def test_reasoning_shared_trace_obj(self) -> None:
        """Formatter renders into a pre-supplied trace_obj without calling send_message.

        The placeholder is now created by the session layer (_ensure_trace_obj) before
        edit_reasoning is invoked. This test verifies:
        - With a valid trace_obj, two back-to-back reasoning blocks both render (edit
          calls are made).
        - The formatter itself never calls send_message (no lazy placeholder creation).
        """
        # Arrange
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=501)
        send_message_mock = AsyncMock(return_value=trace_mock)
        edit_message_mock = AsyncMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = send_message_mock
        adapter.bot.edit_message_text = edit_message_mock

        formatter = _make_formatter(adapter)

        # Act — two back-to-back reasoning blocks; session pre-supplies trace_obj
        for block_id in (_MSG_ID, f"{_MSG_ID}-2"):
            await formatter.edit_reasoning(
                trace_mock, ReasoningStartRenderEvent(message_id=block_id)
            )
            await formatter.edit_reasoning(
                trace_mock,
                ReasoningDeltaRenderEvent(message_id=block_id, delta="thinking…"),
            )
            await formatter.edit_reasoning(
                trace_mock, ReasoningEndRenderEvent(message_id=block_id)
            )

        # Assert — formatter never calls send_message (session owns placeholder)
        assert send_message_mock.await_count == 0, (
            f"Formatter must not create its own placeholder; "
            f"got {send_message_mock.await_count} send_message calls"
        )
        # At least 2 edit calls (one End flush per block)
        assert edit_message_mock.await_count >= 2, (
            f"Expected edits for both reasoning blocks, "
            f"got {edit_message_mock.await_count}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_throttle_bound(self) -> None:
        """Delta edits are throttled: at most ceil(window/interval)+1 API calls.

        Arrange: adapter with show_intermediate=True; time.monotonic controlled;
        session pre-supplies trace_obj (non-None) as per new contract.
        Act: drive Start + 20 Delta events spread across a 2s window + End.
        Assert: edit_message_text count <= ceil(2.0 / STREAMING_EDIT_INTERVAL) + 1
        and >= 1 (at least the final End flush).
        """
        # Arrange
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=502)
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
        adapter.bot.edit_message_text = AsyncMock()

        formatter = _make_formatter(adapter)

        # Spread 20 deltas uniformly across a 2s window
        window = 2.0
        n_deltas = 20
        tick = window / n_deltas  # 0.1s per delta

        # Control time.monotonic in the telegram_formatter module
        times: list[float] = [i * tick for i in range(n_deltas)]
        time_iter = iter(times)

        def fake_monotonic() -> float:
            try:
                return next(time_iter)
            except StopIteration:
                return window

        with patch(
            "lyra.adapters.telegram.telegram_formatter.time.monotonic",
            side_effect=fake_monotonic,
        ):
            # Act — session pre-supplies trace_obj (non-None) as per new contract
            await formatter.edit_reasoning(
                trace_mock, ReasoningStartRenderEvent(message_id=_MSG_ID)
            )
            for i in range(n_deltas):
                await formatter.edit_reasoning(
                    trace_mock,
                    ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta=f"chunk{i}"),
                )
            await formatter.edit_reasoning(
                trace_mock, ReasoningEndRenderEvent(message_id=_MSG_ID)
            )

        # Assert — throttle bound (the "+1" accounts for the final flush at End)
        max_allowed = math.ceil(window / STREAMING_EDIT_INTERVAL) + 1
        actual_edits = adapter.bot.edit_message_text.await_count
        assert actual_edits >= 1, "Expected at least one edit (End flush)"
        assert actual_edits <= max_allowed, (
            f"Too many API edits: {actual_edits} > {max_allowed} "
            f"(window={window}s, interval={STREAMING_EDIT_INTERVAL}s)"
        )

    @pytest.mark.asyncio
    async def test_reasoning_truncation(self) -> None:
        """Delta text > 120 chars is truncated to 117 chars + ellipsis (118 total).

        Arrange: adapter with show_intermediate=True; session pre-supplies trace_obj.
        Act: Start → Delta(200 'x' chars) → End.
        Assert: the edit call receives text of length 118 ending with '…'.
        """
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

        formatter = _make_formatter(adapter)

        # Act — session pre-supplies trace_obj (non-None) as per new contract
        await formatter.edit_reasoning(
            trace_mock, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await formatter.edit_reasoning(
            trace_mock,
            ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="x" * 200),
        )
        await formatter.edit_reasoning(
            trace_mock, ReasoningEndRenderEvent(message_id=_MSG_ID)
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

    # NOTE: show_intermediate=False adapter no-op test removed. The gate now
    # lives upstream on StreamProcessor (SC-6) — when disabled, no Reasoning*
    # events reach this callback. Coverage is in
    # tests/core/test_stream_processor.py::TestReasoning::
    # test_show_intermediate_false_emits_no_reasoning_events.
