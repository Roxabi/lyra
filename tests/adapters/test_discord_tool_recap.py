# pyright: reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""Discord adapter tool recap — single combined message (#1214 / recap-on-top).

Recap + answer share the answer placeholder: no second ``🔧 …`` send.
Embed title/description hold recap (top); answer follows in description.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from factory.core.messaging.render_events import (
    RenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from tests.adapters.conftest import make_dc_inbound_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discord_adapter():
    """Build a DiscordAdapter with mocked internals."""
    from factory.adapters.discord import DiscordAdapter

    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )


def _make_channel() -> tuple[MagicMock, MagicMock]:
    """Return (placeholder_msg, channel) mocked for streaming tests.

    Single-message path:
    1. send_placeholder → reply() → placeholder
    2. send_trace_placeholder → reuses placeholder (no channel.send)
    3. edits (recap + answer) → placeholder.edit(content=..., embed=...)
    """
    placeholder_msg = MagicMock()
    placeholder_msg.id = 42
    placeholder_msg.edit = AsyncMock(return_value=None)

    trigger_msg = MagicMock()
    trigger_msg.reply = AsyncMock(return_value=placeholder_msg)

    channel = AsyncMock()
    # Must not be used for a second recap bubble on tool turns.
    channel.send = AsyncMock(return_value=None)
    channel.get_partial_message = MagicMock(return_value=trigger_msg)

    typing_cm = AsyncMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=False)
    channel.typing = MagicMock(return_value=typing_cm)

    return placeholder_msg, channel


async def _gen(*items: RenderEvent) -> AsyncIterator[RenderEvent]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Test SC8 — multi-tool turn renders recap card via embed
# ---------------------------------------------------------------------------


async def test_multi_tool_turn_renders_recap_card_via_embed() -> None:
    """SC8: edit+bash tools → one message; embed title Done ✅, green, both icons.

    Answer text lands in the same embed description below the recap lines.
    No second channel.send for a separate recap bubble.
    """
    adapter = _make_discord_adapter()
    placeholder_msg, channel = _make_channel()
    adapter._resolve_channel = AsyncMock(return_value=channel)

    original_msg = make_dc_inbound_msg()

    await adapter.send_streaming(
        original_msg,
        _gen(
            RunStartedRenderEvent(run_id="r1"),
            ToolCallStartRenderEvent(tool_call_id="t1", tool_name="edit"),
            ToolCallArgsRenderEvent(
                tool_call_id="t1",
                delta=json.dumps({"path": "src/x.py"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t1"),
            ToolCallStartRenderEvent(tool_call_id="t2", tool_name="bash"),
            ToolCallArgsRenderEvent(
                tool_call_id="t2",
                delta=json.dumps({"command": "ls"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t2"),
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="ok"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        ),
        outbound=None,
    )

    # No second bubble for recap
    assert channel.send.await_count == 0, (
        f"expected no separate recap send, got {channel.send.call_args_list}"
    )

    all_calls = placeholder_msg.edit.call_args_list
    embed_calls = [c for c in all_calls if c.kwargs.get("embed") is not None]

    assert len(embed_calls) >= 1, (
        f"placeholder.edit must be called with embed= at least once. "
        f"All calls: {all_calls}"
    )

    done_header = "\U0001f527 Done ✅"  # 🔧 Done ✅
    done_calls = [
        c
        for c in embed_calls
        if getattr(c.kwargs["embed"], "title", None) == done_header
        and getattr(c.kwargs["embed"], "color", None) == discord.Color.green()
    ]
    embed_titles = [getattr(c.kwargs["embed"], "title", None) for c in embed_calls]
    assert len(done_calls) >= 1, (
        f"No embed call had title={done_header!r} and color=green. "
        f"Embed titles: {embed_titles}"
    )

    edit_icon = "✏️"
    bash_icon = "\U0001f4bb"  # 💻
    matching = [
        c
        for c in done_calls
        if edit_icon in (getattr(c.kwargs["embed"], "description", "") or "")
        and bash_icon in (getattr(c.kwargs["embed"], "description", "") or "")
    ]
    descriptions = [getattr(c.kwargs["embed"], "description", None) for c in done_calls]
    assert len(matching) >= 1, (
        f"Done embed description must contain both '✏️' and '💻'. "
        f"Descriptions: {descriptions}"
    )

    # Answer is in the same embed (recap on top, answer below)
    with_answer = [
        c
        for c in done_calls
        if "ok" in (getattr(c.kwargs["embed"], "description", "") or "")
    ]
    assert len(with_answer) >= 1, (
        f"Done embed description must include answer text 'ok'. "
        f"Descriptions: {descriptions}"
    )


# ---------------------------------------------------------------------------
# Test SC9 — text-only turn does not call trace_obj.edit with embed
# ---------------------------------------------------------------------------


async def test_text_only_turn_does_not_send_discord_trace_placeholder() -> None:
    """SC9: a pure text turn must never trigger a trace placeholder send or
    call trace_obj.edit with an embed= kwarg.

    This test is expected to PASS even at RED state — it is a regression guard.
    """
    # Arrange
    adapter = _make_discord_adapter()

    placeholder_msg = MagicMock()
    placeholder_msg.id = 42
    placeholder_msg.edit = AsyncMock(return_value=None)

    trigger_msg = MagicMock()
    trigger_msg.reply = AsyncMock(return_value=placeholder_msg)

    # track channel.send calls — must be 0 (trace placeholder never sent)
    channel = AsyncMock()
    channel.send = AsyncMock(return_value=None)  # must NOT be called for text-only
    channel.get_partial_message = MagicMock(return_value=trigger_msg)

    typing_cm = AsyncMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=False)
    channel.typing = MagicMock(return_value=typing_cm)

    adapter._resolve_channel = AsyncMock(return_value=channel)
    original_msg = make_dc_inbound_msg()

    # Act — text-only stream (no tool events)
    await adapter.send_streaming(
        original_msg,
        _gen(
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="hello"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        ),
        outbound=None,
    )

    # Assert — channel.send called exactly once (only placeholder, no trace)
    send_calls = channel.send.call_args_list
    _TRACE_PLACEHOLDER_TEXT = "\U0001f527 …"  # "🔧 …"
    trace_sends = [
        c
        for c in send_calls
        if (c.args and c.args[0] == _TRACE_PLACEHOLDER_TEXT)
        or c.kwargs.get("content") == _TRACE_PLACEHOLDER_TEXT
    ]
    assert len(trace_sends) == 0, (
        f"Expected no trace placeholder send, found: {trace_sends}"
    )

    # Assert — placeholder.edit never called with embed= kwarg
    embed_edits = [
        c
        for c in placeholder_msg.edit.call_args_list
        if c.kwargs.get("embed") is not None
    ]
    assert len(embed_edits) == 0, (
        f"Expected no embed edits on text-only turn, found: {embed_edits}"
    )


# ---------------------------------------------------------------------------
# Test smoke — intermediate edit uses blue color and "🔧 Working…" title
# ---------------------------------------------------------------------------


async def test_intermediate_edit_uses_blue_color_and_working_title() -> None:
    """Smoke: intermediate recap (done=False) → blue embed '🔧 Working…' on placeholder."""
    adapter = _make_discord_adapter()
    placeholder_msg, channel = _make_channel()
    adapter._resolve_channel = AsyncMock(return_value=channel)
    original_msg = make_dc_inbound_msg()

    _call_count = 0

    def _fake_monotonic() -> float:
        nonlocal _call_count
        _call_count += 1
        return float(_call_count * 10)

    _patch_target = "factory.outbound.emitter.time.monotonic"
    with patch(_patch_target, _fake_monotonic):
        await adapter.send_streaming(
            original_msg,
            _gen(
                RunStartedRenderEvent(run_id="r2"),
                ToolCallStartRenderEvent(tool_call_id="u1", tool_name="bash"),
                ToolCallArgsRenderEvent(
                    tool_call_id="u1",
                    delta=json.dumps({"command": "ls -la"}),
                ),
                ToolCallEndRenderEvent(tool_call_id="u1"),
                TextStartRenderEvent(message_id="msg-2"),
                TextDeltaRenderEvent(message_id="msg-2", delta="ok"),
                TextEndRenderEvent(message_id="msg-2"),
                RunFinishedRenderEvent(run_id="r2", outcome="success"),
            ),
            outbound=None,
        )

    all_calls = placeholder_msg.edit.call_args_list
    embed_calls = [c for c in all_calls if c.kwargs.get("embed") is not None]

    assert len(embed_calls) >= 1, (
        f"placeholder.edit must be called with embed= at least once. "
        f"All calls: {all_calls}"
    )

    working_header = "\U0001f527 Working…"  # 🔧 Working…
    intermediate_calls = [
        c
        for c in embed_calls
        if getattr(c.kwargs["embed"], "title", None) == working_header
        and getattr(c.kwargs["embed"], "color", None) == discord.Color.blue()
    ]
    titles = [getattr(c.kwargs["embed"], "title", None) for c in embed_calls]
    colors = [getattr(c.kwargs["embed"], "color", None) for c in embed_calls]
    assert len(intermediate_calls) >= 1, (
        f"Expected at least one embed call with title={working_header!r} and "
        f"color=blue. Titles: {titles}, Colors: {colors}"
    )
