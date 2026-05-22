"""Integration tests for Discord adapter Reasoning rendering (SC-16, T13).

Covers _render_reasoning via PlatformCallbacks.edit_reasoning for:
- Reasoning callback edits the session-supplied trace_obj
  (lazy placeholder creation moved upstream to StreamingSession in #1214)
- Edit throttle bound
- Text truncation

The show_intermediate=False gate lives upstream on StreamProcessor (SC-6) — no
Reasoning* events reach this callback when disabled, so adapter-level coverage
is not needed here (see test_stream_processor.py::TestReasoning).
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL
from tests.adapters.conftest import make_dc_inbound_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MSG_ID = "r-dc-test-1"


def _make_discord_adapter() -> DiscordAdapter:
    """Build a DiscordAdapter with a MagicMock hub."""
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )


def _make_messageable_with_trace_obj() -> tuple[AsyncMock, AsyncMock]:
    """Return (mock_messageable, mock_trace_obj).

    mock_messageable.send("🔧 …") returns mock_trace_obj.
    mock_trace_obj.edit is an AsyncMock to track reasoning edit calls.
    """
    trace_obj = AsyncMock()
    trace_obj.id = 601
    trace_obj.edit = AsyncMock()

    messageable = AsyncMock()
    messageable.send = AsyncMock(return_value=trace_obj)
    return messageable, trace_obj


# ---------------------------------------------------------------------------
# T13 — Discord reasoning rendering (4 tests)
# ---------------------------------------------------------------------------


class TestDiscordReasoningRendering:
    """T13 — Discord adapter Reasoning event rendering (SC-16)."""

    @pytest.mark.asyncio
    async def test_reasoning_uses_session_supplied_trace_obj(self) -> None:
        """Reasoning callback edits the session-supplied trace_obj.

        Post-#1214: lazy placeholder creation moved upstream to
        ``StreamingSession._ensure_trace_obj``. The callback no longer
        calls ``messageable.send`` itself — it must edit whatever
        ``trace_obj`` the session passes in. Two back-to-back reasoning
        blocks must therefore both edit the same session-supplied
        trace_obj without producing any extra ``messageable.send`` calls.
        """
        from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_discord_adapter()
        messageable, trace_obj = _make_messageable_with_trace_obj()
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
        callbacks = build_streaming_callbacks(adapter, original_msg, None)

        # Act — two back-to-back reasoning blocks; session passes trace_obj.
        for block_id in (_MSG_ID, f"{_MSG_ID}-2"):
            await callbacks.edit_reasoning(
                trace_obj, ReasoningStartRenderEvent(message_id=block_id)
            )
            await callbacks.edit_reasoning(
                trace_obj,
                ReasoningDeltaRenderEvent(message_id=block_id, delta="thinking…"),
            )
            await callbacks.edit_reasoning(
                trace_obj, ReasoningEndRenderEvent(message_id=block_id)
            )

        # Callback never sends its own placeholder anymore.
        assert messageable.send.await_count == 0, (
            "edit_reasoning must not send its own placeholder "
            "(session._ensure_trace_obj owns lazy-create)"
        )
        # And it edited the session-supplied trace_obj at least once.
        assert trace_obj.edit.await_count >= 1, (
            "Expected ≥1 trace_obj.edit call across the two reasoning blocks"
        )

    @pytest.mark.asyncio
    async def test_reasoning_throttle_bound(self) -> None:
        """Delta edits are throttled: at most ceil(window/interval)+1 API calls.

        Arrange: adapter with show_intermediate=True; time.monotonic controlled.
        Act: drive Start + 20 Delta events spread across a 2s window + End.
        Assert: trace_obj.edit count <= ceil(2.0 / STREAMING_EDIT_INTERVAL) + 1.
        """
        from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_discord_adapter()
        messageable, trace_obj = _make_messageable_with_trace_obj()
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
        callbacks = build_streaming_callbacks(adapter, original_msg, None)

        # Spread 20 deltas uniformly across a 2s window
        window = 2.0
        n_deltas = 20
        tick = window / n_deltas  # 0.1s per delta

        times: list[float] = [i * tick for i in range(n_deltas)]
        time_iter = iter(times)

        def fake_monotonic() -> float:
            try:
                return next(time_iter)
            except StopIteration:
                return window

        with patch(
            "lyra.adapters.discord.discord_outbound.time.monotonic",
            side_effect=fake_monotonic,
        ):
            # Act — session supplies trace_obj (post-#1214 contract).
            await callbacks.edit_reasoning(
                trace_obj, ReasoningStartRenderEvent(message_id=_MSG_ID)
            )
            for i in range(n_deltas):
                await callbacks.edit_reasoning(
                    trace_obj,
                    ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta=f"chunk{i}"),
                )
            await callbacks.edit_reasoning(
                trace_obj, ReasoningEndRenderEvent(message_id=_MSG_ID)
            )

        # Assert — throttle bound (the "+1" accounts for the final flush at End)
        max_allowed = math.ceil(window / STREAMING_EDIT_INTERVAL) + 1
        actual_edits = trace_obj.edit.await_count
        assert actual_edits <= max_allowed, (
            f"Too many API edits: {actual_edits} > {max_allowed} "
            f"(window={window}s, interval={STREAMING_EDIT_INTERVAL}s)"
        )

    @pytest.mark.asyncio
    async def test_reasoning_truncation(self) -> None:
        """Delta text > 120 chars is truncated to 117 chars + ellipsis (118 total).

        Arrange: adapter with show_intermediate=True.
        Act: Start → Delta(200 'x' chars) → End.
        Assert: the edit call receives text ending with '…'.
        """
        from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_discord_adapter()
        messageable = AsyncMock()
        edit_calls: list[str] = []

        trace_obj = AsyncMock()
        trace_obj.id = 602

        async def capture_edit(*, content: object, embed: object) -> None:
            del embed
            assert isinstance(content, str)
            edit_calls.append(content)

        trace_obj.edit = capture_edit
        messageable.send = AsyncMock(return_value=trace_obj)
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
        callbacks = build_streaming_callbacks(adapter, original_msg, None)

        # Act — session supplies trace_obj (post-#1214 contract).
        await callbacks.edit_reasoning(
            trace_obj, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await callbacks.edit_reasoning(
            trace_obj,
            ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="x" * 200),
        )
        await callbacks.edit_reasoning(
            trace_obj, ReasoningEndRenderEvent(message_id=_MSG_ID)
        )

        # Assert — at least one edit call was made
        assert len(edit_calls) >= 1, "Expected at least one trace_obj.edit call"
        last_text = edit_calls[-1]
        # The text passed to trace_obj.edit is _dim_italic(truncated) = "*...*".
        # Evidence of truncation: text contains the ellipsis character.
        assert "…" in last_text, (
            f"Expected truncation ellipsis in edit content, got: {last_text!r}"
        )

    # NOTE: show_intermediate=False adapter no-op test removed. The gate now
    # lives upstream on StreamProcessor (SC-6) — when disabled, no Reasoning*
    # events reach this callback. Coverage is in
    # tests/core/test_stream_processor.py::TestReasoning::
    # test_show_intermediate_false_emits_no_reasoning_events.
