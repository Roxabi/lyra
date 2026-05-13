"""Integration tests for Discord adapter Reasoning rendering (SC-16, T13).

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

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.shared._shared_streaming_state import STREAMING_EDIT_INTERVAL
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
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
    async def test_reasoning_lazy_placeholder(self) -> None:
        """Reasoning trace placeholder is sent exactly ONCE across Start+Delta+End.

        Arrange: adapter with show_intermediate=True, no pre-existing trace.
        Act: drive ReasoningStart → ReasoningDelta → ReasoningEnd.
        Assert: messageable.send called once (trace placeholder), not more.
        """
        from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_discord_adapter()
        messageable, _trace_obj = _make_messageable_with_trace_obj()
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=True
        )

        # Act — full Reasoning triplet (no prior tool events → _trace_obj=None)
        await callbacks.edit_reasoning(
            None, ReasoningStartRenderEvent(message_id=_MSG_ID)
        )
        await callbacks.edit_reasoning(
            None, ReasoningDeltaRenderEvent(message_id=_MSG_ID, delta="thinking…")
        )
        await callbacks.edit_reasoning(
            None, ReasoningEndRenderEvent(message_id=_MSG_ID)
        )

        # Assert — channel.send called exactly once for trace placeholder
        # (_send_trace_placeholder calls messageable.send("🔧 …"))
        assert messageable.send.await_count == 1, (
            f"Expected 1 messageable.send call (trace placeholder), "
            f"got {messageable.send.await_count}"
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
        callbacks = build_streaming_callbacks(
            adapter, original_msg, None, show_intermediate=True
        )

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
            assert isinstance(content, str)
            edit_calls.append(content)

        trace_obj.edit = capture_edit
        messageable.send = AsyncMock(return_value=trace_obj)
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
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
        assert len(edit_calls) >= 1, "Expected at least one trace_obj.edit call"
        last_text = edit_calls[-1]
        # The text passed to trace_obj.edit is _dim_italic(truncated) = "*...*".
        # Evidence of truncation: text contains the ellipsis character.
        assert "…" in last_text, (
            f"Expected truncation ellipsis in edit content, got: {last_text!r}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_show_intermediate_false_noop(self) -> None:
        """show_intermediate=False: Reasoning events produce zero API calls.

        Arrange: adapter with show_intermediate=False.
        Act: drive full Reasoning triplet.
        Assert: messageable.send NOT called, trace_obj.edit NOT called.
        """
        from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

        # Arrange
        adapter = _make_discord_adapter()
        messageable, trace_obj = _make_messageable_with_trace_obj()
        adapter._resolve_channel = AsyncMock(return_value=messageable)

        original_msg = make_dc_inbound_msg()
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

        # Assert — no channel sends, no trace edits
        messageable.send.assert_not_awaited()
        trace_obj.edit.assert_not_awaited()
