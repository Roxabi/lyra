# pyright: reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""RED tests for Discord adapter tool recap card rendering (#1214 T11).

These tests expose the gap: ``build_streaming_callbacks`` in ``discord_outbound.py``
does NOT yet pass ``edit_tool_recap=...`` to ``PlatformCallbacks``.  The default
no-op is used, so ``trace_obj.edit`` is never called with ``embed=...`` kwargs.

Tests SC8 (multi-tool embed parity) and the intermediate-color smoke MUST FAIL
on the unmodified codebase.  Test SC9 (text-only regression guard) will PASS
even at RED state — that is intentional.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from lyra.core.messaging.render_events import (
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

_TRACE_MSG_ID = 999


def _make_discord_adapter():
    """Build a DiscordAdapter with mocked internals."""
    from lyra.adapters.discord import DiscordAdapter

    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )


def _make_trace_obj() -> MagicMock:
    """Return a fake trace placeholder object (simulates a discord.Message)."""
    trace_obj = MagicMock()
    trace_obj.edit = AsyncMock(return_value=None)
    trace_obj.id = _TRACE_MSG_ID
    return trace_obj


def _make_channel(trace_obj: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Return (placeholder_msg, channel) mocked for streaming tests.

    build_streaming_callbacks uses ``msg_obj.reply()`` (via get_partial_message)
    for the response placeholder (because message_id=555, should_reply=True),
    and ``messageable.send("🔧 …")`` for the trace placeholder.

    Call order:
    1. _send_placeholder() → channel.get_partial_message(555).reply(...) → placeholder
    2. _send_trace_placeholder() → channel.send("🔧 …") → trace_obj
    3. _deliver_final / edit_placeholder_text → placeholder.edit(content=...,
       embed=None)
    """
    placeholder_msg = MagicMock()
    placeholder_msg.id = 42
    placeholder_msg.edit = AsyncMock(return_value=None)

    trigger_msg = MagicMock()
    trigger_msg.reply = AsyncMock(return_value=placeholder_msg)

    channel = AsyncMock()
    # channel.send is used only for the trace placeholder
    channel.send = AsyncMock(return_value=trace_obj)
    # get_partial_message returns the trigger msg whose reply() returns the placeholder
    channel.get_partial_message = MagicMock(return_value=trigger_msg)

    # typing() must be an async context manager
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
    """SC8: a turn with edit + bash tool calls must call trace_obj.edit at least
    once with an embed= kwarg whose title is '🔧 Done ✅', color is green,
    and description contains both '✏️' and '💻'.

    This test FAILS on the unmodified codebase because ``edit_tool_recap`` is
    the default no-op, so trace_obj.edit is never called with embed= kwargs.
    """
    # Arrange
    adapter = _make_discord_adapter()
    trace_obj = _make_trace_obj()
    _placeholder_msg, channel = _make_channel(trace_obj)
    adapter._resolve_channel = AsyncMock(return_value=channel)

    original_msg = make_dc_inbound_msg()

    # Act — drive a streaming session with edit + bash tool calls
    await adapter.send_streaming(
        original_msg,
        _gen(
            RunStartedRenderEvent(run_id="r1"),
            # Tool 1: edit call
            ToolCallStartRenderEvent(tool_call_id="t1", tool_name="edit"),
            ToolCallArgsRenderEvent(
                tool_call_id="t1",
                delta=json.dumps({"path": "src/x.py"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t1"),
            # Tool 2: bash call
            ToolCallStartRenderEvent(tool_call_id="t2", tool_name="bash"),
            ToolCallArgsRenderEvent(
                tool_call_id="t2",
                delta=json.dumps({"command": "ls"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t2"),
            # Text response
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="ok"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        ),
        outbound=None,
    )

    # Assert — trace_obj.edit was called at least once with embed= kwarg
    all_calls = trace_obj.edit.call_args_list
    embed_calls = [c for c in all_calls if c.kwargs.get("embed") is not None]

    assert len(embed_calls) >= 1, (
        f"trace_obj.edit must be called at least once with embed= kwarg. "
        f"All calls: {all_calls}"
    )

    # At least one call must have title='🔧 Done ✅' and color=green
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

    # The description of the done embed must contain both tool icons
    edit_icon = "✏️"  # ✏️
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
    """Smoke: at least one intermediate recap edit (done=False) fires with
    embed.color == discord.Color.blue() and embed.title == '🔧 Working…'.

    Time is monkeypatched so the first ToolCallEnd always passes the debounce
    threshold, guaranteeing an intermediate edit before the final done=True.

    This test FAILS on the unmodified codebase because edit_tool_recap is a
    no-op and trace_obj.edit is never called with embed= kwargs.
    """
    # Arrange
    adapter = _make_discord_adapter()
    trace_obj = _make_trace_obj()
    _placeholder_msg, channel = _make_channel(trace_obj)
    adapter._resolve_channel = AsyncMock(return_value=channel)
    original_msg = make_dc_inbound_msg()

    # Patch time.monotonic so that the debounce always fires:
    # first call returns 0.0 (last_recap_edit is None → always fires on first event),
    # subsequent calls return large values to guarantee the interval is exceeded.
    _call_count = 0

    def _fake_monotonic() -> float:
        nonlocal _call_count
        _call_count += 1
        # 10s gap between each call → always past debounce interval
        return float(_call_count * 10)

    _patch_target = "lyra.outbound.emitter.time.monotonic"
    with patch(_patch_target, _fake_monotonic):
        # Act — drive tool events; debounce is bypassed by time patch
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

    # Assert — at least one embed call with blue color and "Working…" title
    all_calls = trace_obj.edit.call_args_list
    embed_calls = [c for c in all_calls if c.kwargs.get("embed") is not None]

    assert len(embed_calls) >= 1, (
        f"trace_obj.edit must be called at least once with embed= kwarg. "
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
