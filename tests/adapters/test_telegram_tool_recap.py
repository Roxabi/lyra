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

import pytest

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
from tests.adapters.conftest import (
    _make_telegram_adapter,
    _make_telegram_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_MSG_ID = 999


def _rich_html(call) -> str:
    rich = call.kwargs.get("rich_message")
    if rich is None:
        return ""
    return rich.html or ""


def _rich_markdown(call) -> str:
    rich = call.kwargs.get("rich_message")
    if rich is None:
        return ""
    return rich.markdown or ""


def _recap_payload(call) -> str:
    """Recap content lives in <tg-thinking> html (#1952); fallback uses markdown."""
    html = _rich_html(call)
    if html:
        return html
    return _rich_markdown(call)


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
    bot.send_rich_message = AsyncMock(side_effect=[placeholder_msg, trace_msg])
    bot.send_rich_message_draft = AsyncMock(return_value=True)
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
    - At least one recap edit uses <tg-thinking> html with '🔧 Done ✅' header.
    - At least one call contains '✏️' (edit tool icon) AND '💻' (bash icon).

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
        if done_header in _recap_payload(c)
        and edit_icon in _recap_payload(c)
        and bash_icon in _recap_payload(c)
    ]
    assert len(matching) >= 1, (
        f"No edit_message_text call contained recap header + icons. "
        f"Recap calls texts: {[_recap_payload(c) for c in recap_calls]}"
    )
    assert any("<tg-thinking>" in _rich_html(c) for c in matching), (
        "Recap edits must use <tg-thinking> html in rich mode"
    )


# ---------------------------------------------------------------------------
# Test SC9 — text-only turn does not call edit_message_text with recap content
# ---------------------------------------------------------------------------


async def test_text_only_turn_does_not_send_telegram_trace_placeholder() -> None:
    """SC9: a pure text turn must never trigger a trace placeholder or recap edit.

    Asserts:
    - No bot.send_rich_message call has trace placeholder text.
    - bot.edit_message_text is never called with text containing recap header.

    This test is expected to PASS even at RED state — it is a regression guard.
    """
    # Arrange
    adapter = _make_telegram_adapter()
    placeholder_msg = MagicMock()
    placeholder_msg.message_id = 42

    bot = MagicMock()
    bot.send_rich_message = AsyncMock(return_value=placeholder_msg)
    bot.send_rich_message_draft = AsyncMock(return_value=True)
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
    send_calls = bot.send_rich_message.call_args_list
    trace_sends = [
        c
        for c in send_calls
        if _TRACE_PLACEHOLDER_TEXT in _recap_payload(c)
        or _rich_markdown(c) == _TRACE_PLACEHOLDER_TEXT
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
        if recap_header in _recap_payload(c) or recap_working in _recap_payload(c)
    ]
    assert len(recap_edits) == 0, (
        f"Expected no recap card edit_message_text calls, found: {recap_edits}"
    )


# ---------------------------------------------------------------------------
# Test smoke — recap text uses rich_message (no MarkdownV2 escaping)
# ---------------------------------------------------------------------------


async def test_recap_text_is_markdownv2_escaped() -> None:
    """Smoke: bash recap content is sent via <tg-thinking> html (unescaped markdown).

    Drive a single bash tool call with command 'echo *hi*' (contains MarkdownV2-
    special '*'). Thinking-block html preserves the raw command.

    This test FAILS on the unmodified codebase because edit_tool_recap is a no-op
    and bot.edit_message_text is never called.
    """
    # Arrange — response placeholder is first send_rich_message; trace is second
    adapter = _make_telegram_adapter()

    placeholder_msg = MagicMock()
    placeholder_msg.message_id = 42
    trace_msg = MagicMock()
    trace_msg.message_id = _TRACE_MSG_ID

    bot = MagicMock()
    bot.send_rich_message = AsyncMock(side_effect=[placeholder_msg, trace_msg])
    bot.send_rich_message_draft = AsyncMock(return_value=True)
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

    # Rich path: no parse_mode; thinking html carries unescaped special chars
    for c in recap_calls:
        assert c.kwargs.get("parse_mode") is None
        assert c.kwargs.get("rich_message") is not None
        assert "<tg-thinking>" in _rich_html(c)

    # The text must contain the bash icon (proof it is recap content, not some
    # other edit — e.g. the response placeholder edit)
    bash_icon = "\U0001f4bb"  # 💻
    recap_with_bash = [c for c in recap_calls if bash_icon in _recap_payload(c)]
    texts = [_recap_payload(c) for c in recap_calls]
    assert len(recap_with_bash) >= 1, (
        f"No recap call contained bash icon. Texts: {texts}"
    )
    assert any("*hi*" in t for t in texts), (
        f"Expected unescaped '*hi*' in recap payload, got: {texts}"
    )


async def test_recap_fallback_mode_uses_markdownv2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC4: rich disabled → edit_tool_recap keeps MarkdownV2 fallback path."""
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_MESSAGES", "0")
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

    await adapter.send_streaming(
        original_msg,
        _gen(
            RunStartedRenderEvent(run_id="r3"),
            ToolCallStartRenderEvent(tool_call_id="t3", tool_name="bash"),
            ToolCallArgsRenderEvent(
                tool_call_id="t3",
                delta=json.dumps({"command": "pwd"}),
            ),
            ToolCallEndRenderEvent(tool_call_id="t3"),
            TextStartRenderEvent(message_id="msg-3"),
            TextDeltaRenderEvent(message_id="msg-3", delta="ok"),
            TextEndRenderEvent(message_id="msg-3"),
            RunFinishedRenderEvent(run_id="r3", outcome="success"),
        ),
        outbound=None,
    )

    recap_calls = [
        c
        for c in bot.edit_message_text.call_args_list
        if c.kwargs.get("message_id") == _TRACE_MSG_ID
    ]
    assert len(recap_calls) >= 1
    for c in recap_calls:
        assert c.kwargs.get("rich_message") is None
        assert c.kwargs.get("parse_mode") == "MarkdownV2"
