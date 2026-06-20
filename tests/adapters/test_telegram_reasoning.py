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

from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from factory.outbound.throttle import STREAMING_EDIT_INTERVAL
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
    from factory.adapters.telegram.telegram_formatter import TelegramFormatter

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
        send_rich_mock = AsyncMock(return_value=trace_mock)
        edit_message_mock = AsyncMock()
        adapter.bot = MagicMock()
        adapter.bot.send_rich_message = send_rich_mock
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
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

        # Assert — formatter never calls send APIs (session owns placeholder)
        assert send_rich_mock.await_count == 0, (
            f"Formatter must not create its own placeholder; "
            f"got {send_rich_mock.await_count} send_rich_message calls"
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
        adapter.bot.send_rich_message = AsyncMock(return_value=trace_mock)
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
            "factory.outbound._reasoning_accum.time.monotonic",
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
    async def test_reasoning_rich_mode_full_text_no_italic(self) -> None:
        """Rich mode: full reasoning text streamed into <tg-thinking> — no truncation, no asterisks.

        Fixes #1949 (dim_italic asterisks literal in HTML) and #1951 (120-char cap
        unnecessary when tg-thinking is collapsible).

        Act: Start → Delta(200 'x' chars) → End.
        Assert:
        - HTML contains the full 200 chars (no '…').
        - HTML does NOT contain asterisks (no dim_italic wrapping).
        - HTML is wrapped in <tg-thinking>…</tg-thinking>.
        """
        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=503)
        adapter.bot = MagicMock()
        adapter.bot.send_rich_message = AsyncMock(return_value=trace_mock)
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
        edit_calls: list[str] = []

        async def capture_edit(**kwargs: object) -> None:
            rich = kwargs.get("rich_message")
            html = getattr(rich, "html", None) if rich is not None else None
            if html is not None:
                edit_calls.append(html)

        adapter.bot.edit_message_text = capture_edit
        formatter = _make_formatter(adapter)

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

        assert len(edit_calls) >= 1, "Expected at least one edit_message_text call"
        last = edit_calls[-1]
        assert "…" not in last, f"Rich mode must not truncate reasoning; got: {last!r}"
        assert "*" not in last, f"Rich mode must not wrap in asterisks (#1949); got: {last!r}"
        assert last.startswith("<tg-thinking>"), f"Expected tg-thinking block; got: {last!r}"
        assert "x" * 200 in last, "Full 200-char text must appear in rich thinking block"

    @pytest.mark.asyncio
    async def test_reasoning_markdownv2_truncation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MarkdownV2 fallback path: text > 120 chars still truncated with '…'.

        Truncation stays in the MarkdownV2 path — only the rich path skips it.
        """
        monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "0")

        adapter = _make_telegram_adapter()
        trace_mock = _make_trace_send_mock(message_id=504)
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock(return_value=trace_mock)
        edit_calls: list[str] = []

        async def capture_edit(**kwargs: object) -> None:
            text = kwargs.get("text", "")
            if isinstance(text, str):
                edit_calls.append(text)

        adapter.bot.edit_message_text = capture_edit
        formatter = _make_formatter(adapter)

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

        assert len(edit_calls) >= 1, "Expected at least one edit call"
        assert "…" in edit_calls[-1], (
            f"MarkdownV2 path must still truncate at 120 chars; got: {edit_calls[-1]!r}"
        )

    # NOTE: show_intermediate=False adapter no-op test removed. The gate now
    # lives upstream on StreamProcessor (SC-6) — when disabled, no Reasoning*
    # events reach this callback. Coverage is in
    # tests/core/test_stream_processor.py::TestReasoning::
    # test_show_intermediate_false_emits_no_reasoning_events.
