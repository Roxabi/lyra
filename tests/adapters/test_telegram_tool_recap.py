# pyright: reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""RED tests for Telegram adapter tool recap card rendering (#1214 T7).

These tests expose the gap: ``build_streaming_callbacks`` in ``telegram_outbound.py``
does NOT yet pass ``edit_tool_recap=...`` to ``PlatformCallbacks``.  The default
no-op is used, so ``bot.edit_message_text`` is never called with recap content.

All assertion-positive tests (SC7, smoke) MUST FAIL on the unmodified codebase.
Test SC9 (text-only regression guard) will pass even at RED state — that is
intentional, it is a regression guard.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

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
from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_MSG_ID = 999


def _make_bot_mocks() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (bot_mock, trace_send_return, placeholder_send_return).

    Call order inside StreamingSession:
    1. send_placeholder() → response placeholder (message_id=42)
    2. send_trace_placeholder() on first ToolCallStart → trace (message_id=999)

    - bot.edit_message_text: AsyncMock, assertions target this.
    """
    placeholder_msg = MagicMock()
    placeholder_msg.message_id = 42

    trace_msg = MagicMock()
    trace_msg.message_id = _TRACE_MSG_ID

    bot = MagicMock()
    # Response placeholder first, trace placeholder second
    bot.send_message = AsyncMock(side_effect=[placeholder_msg, trace_msg])
    bot.edit_message_text = AsyncMock(return_value=None)
    return bot, trace_msg, placeholder_msg


async def _gen(*items: RenderEvent) -> AsyncIterator[RenderEvent]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Test SC7 — multi-tool turn renders recap card via edit_message_text
# ---------------------------------------------------------------------------


async def test_multi_tool_turn_renders_recap_card_via_edit_message_text() -> None:
    """SC7: a turn with edit + bash tool calls must call bot.edit_message_text
    at least once on the trace placeholder with recap card content.

    Asserts:
    - bot.edit_message_text was called at least once.
    - At least one call targets the trace placeholder message_id.
    - At least one such call's ``text`` contains the recap header '🔧 Done ✅'.
    - At least one call contains '✏️' (edit tool icon) AND '💻' (bash icon).
    - parse_mode is 'MarkdownV2' on those calls.

    This test FAILS on the unmodified codebase because ``edit_tool_recap`` is the
    default no-op, so bot.edit_message_text is never called with recap content.
    """
    # Arrange
    adapter = _make_telegram_adapter()
    bot, _trace_msg, _placeholder_msg = _make_bot_mocks()
    adapter.bot = bot
    original_msg = _make_telegram_message()

    # Act — drive a streaming session with edit + bash tool calls
    await adapter.send_streaming(
        original_msg,
        _gen(
            RunStartedRenderEvent(run_id="r1"),
            # Tool 1: edit call
            ToolCallStartRenderEvent(tool_call_id="t1", tool_name="edit"),
            ToolCallArgsRenderEvent(
                tool_call_id="t1",
                delta=json.dumps({"path": "src/foo.py"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t1"),
            # Tool 2: bash call
            ToolCallStartRenderEvent(tool_call_id="t2", tool_name="bash"),
            ToolCallArgsRenderEvent(
                tool_call_id="t2",
                delta=json.dumps({"command": "ls -la"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t2"),
            # Text response
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="done"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        ),
        outbound=None,
    )

    # Assert — edit_message_text was called at least once
    assert bot.edit_message_text.await_count >= 1, (
        "bot.edit_message_text must be called at least once for recap rendering"
    )

    # Collect all calls targeting the trace placeholder message_id
    all_calls = bot.edit_message_text.call_args_list
    recap_calls = [
        c
        for c in all_calls
        if (c.kwargs.get("message_id") == _TRACE_MSG_ID)
        or (len(c.args) >= 2 and c.args[1] == _TRACE_MSG_ID)
    ]
    assert len(recap_calls) >= 1, (
        f"No edit_message_text call targeted trace message_id={_TRACE_MSG_ID}. "
        f"All calls: {all_calls}"
    )

    # At least one call must contain the recap header AND tool icons
    done_header = "\U0001f527 Done ✅"  # 🔧 Done ✅
    edit_icon = "✏️"  # ✏️
    bash_icon = "\U0001f4bb"  # 💻

    matching = [
        c
        for c in recap_calls
        if done_header in (c.kwargs.get("text") or "")
        and edit_icon in (c.kwargs.get("text") or "")
        and bash_icon in (c.kwargs.get("text") or "")
    ]
    assert len(matching) >= 1, (
        f"No edit_message_text call contained recap header + icons. "
        f"Recap calls texts: {[c.kwargs.get('text') for c in recap_calls]}"
    )

    # parse_mode must be MarkdownV2 on those calls
    for c in matching:
        assert c.kwargs.get("parse_mode") == "MarkdownV2", (
            f"Expected parse_mode='MarkdownV2', got {c.kwargs.get('parse_mode')!r}"
        )


# ---------------------------------------------------------------------------
# Test SC9 — text-only turn does not call edit_message_text with recap content
# ---------------------------------------------------------------------------


async def test_text_only_turn_does_not_send_telegram_trace_placeholder() -> None:
    """SC9: a pure text turn must never trigger a trace placeholder or recap edit.

    Asserts:
    - No bot.send_message call has text='🔧 …' (the trace placeholder text).
    - bot.edit_message_text is never called with text containing recap header.

    This test is expected to PASS even at RED state — it is a regression guard.
    """
    # Arrange
    adapter = _make_telegram_adapter()
    placeholder_msg = MagicMock()
    placeholder_msg.message_id = 42

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=placeholder_msg)
    bot.edit_message_text = AsyncMock(return_value=None)
    adapter.bot = bot
    original_msg = _make_telegram_message()

    # Act — text-only stream
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

    # Assert — no trace placeholder send (text="\U0001f527 …")
    _TRACE_PLACEHOLDER_TEXT = "\U0001f527 …"  # "🔧 …"
    send_calls = bot.send_message.call_args_list
    trace_sends = [
        c
        for c in send_calls
        if c.kwargs.get("text") == _TRACE_PLACEHOLDER_TEXT
        or (c.args and c.args[0] == _TRACE_PLACEHOLDER_TEXT)
    ]
    assert len(trace_sends) == 0, (
        f"Expected no trace placeholder send, but found: {trace_sends}"
    )

    # Assert — no edit_message_text call with recap header
    recap_header = "\U0001f527 Done ✅"  # 🔧 Done ✅
    recap_working = "\U0001f527 Working…"  # 🔧 Working…
    edit_calls = bot.edit_message_text.call_args_list
    recap_edits = [
        c
        for c in edit_calls
        if recap_header in (c.kwargs.get("text") or "")
        or recap_working in (c.kwargs.get("text") or "")
    ]
    assert len(recap_edits) == 0, (
        f"Expected no recap card edit_message_text calls, found: {recap_edits}"
    )


# ---------------------------------------------------------------------------
# Test smoke — recap text is MarkdownV2 escaped via _render_text
# ---------------------------------------------------------------------------


async def test_recap_text_is_markdownv2_escaped() -> None:
    """Smoke: when a bash command is in the recap, the edit_message_text call
    uses parse_mode='MarkdownV2' and the text has been run through _render_text.

    Drive a single bash tool call with command 'echo *hi*' (contains MarkdownV2-
    special '*'). The rendered recap line wraps the command in a code span, so
    backtick escaping applies. The key assertion is parse_mode='MarkdownV2'.

    This test FAILS on the unmodified codebase because edit_tool_recap is a no-op
    and bot.edit_message_text is never called.
    """
    # Arrange — response placeholder is the FIRST send_message call; trace is second
    adapter = _make_telegram_adapter()

    placeholder_msg = MagicMock()
    placeholder_msg.message_id = 42
    trace_msg = MagicMock()
    trace_msg.message_id = _TRACE_MSG_ID

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[placeholder_msg, trace_msg])
    bot.edit_message_text = AsyncMock(return_value=None)
    adapter.bot = bot
    original_msg = _make_telegram_message()

    # Act — single bash tool with MarkdownV2-special chars
    await adapter.send_streaming(
        original_msg,
        _gen(
            RunStartedRenderEvent(run_id="r2"),
            ToolCallStartRenderEvent(tool_call_id="u1", tool_name="bash"),
            ToolCallArgsRenderEvent(
                tool_call_id="u1",
                delta=json.dumps({"command": "echo *hi*"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="u1"),
            TextStartRenderEvent(message_id="msg-2"),
            TextDeltaRenderEvent(message_id="msg-2", delta="ok"),
            TextEndRenderEvent(message_id="msg-2"),
            RunFinishedRenderEvent(run_id="r2", outcome="success"),
        ),
        outbound=None,
    )

    # Assert — at least one edit_message_text call on the trace placeholder
    assert bot.edit_message_text.await_count >= 1, (
        "bot.edit_message_text must be called at least once for recap rendering"
    )

    all_calls = bot.edit_message_text.call_args_list
    recap_calls = [
        c for c in all_calls if (c.kwargs.get("message_id") == _TRACE_MSG_ID)
    ]
    assert len(recap_calls) >= 1, (
        f"No edit_message_text call targeted trace message_id={_TRACE_MSG_ID}"
    )

    # parse_mode must be 'MarkdownV2' — this confirms _render_text was used
    for c in recap_calls:
        assert c.kwargs.get("parse_mode") == "MarkdownV2", (
            f"Expected parse_mode='MarkdownV2', got {c.kwargs.get('parse_mode')!r}"
        )

    # The text must contain the bash icon (proof it is recap content, not some
    # other edit — e.g. the response placeholder edit)
    bash_icon = "\U0001f4bb"  # 💻
    recap_with_bash = [
        c for c in recap_calls if bash_icon in (c.kwargs.get("text") or "")
    ]
    texts = [c.kwargs.get("text") for c in recap_calls]
    assert len(recap_with_bash) >= 1, (
        f"No recap call contained bash icon. Texts: {texts}"
    )
