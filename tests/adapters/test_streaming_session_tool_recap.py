# pyright: reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""Tests for StreamingSession tool recap callback wiring (#1214 T6).

Migrated in S7 (#1501): PlatformCallbacks dataclass replaced with
MagicMock OutboundFormatter. Test semantics are preserved.
"""

from __future__ import annotations

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
from factory.outbound.emitter import OutboundEmitter as StreamingSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_formatter(**overrides) -> MagicMock:
    """Build a mock OutboundFormatter with all fields mocked, including edit_tool_recap."""  # noqa: E501
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.chunk = MagicMock(side_effect=lambda t: [t] if t else [])
    fmt.get_msg = MagicMock(side_effect=lambda _key, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    fmt.send_trace_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.send_message = AsyncMock(return_value=99)
    fmt.send_fallback = AsyncMock(return_value=77)
    fmt.edit_reasoning = AsyncMock()
    fmt.edit_tool_recap = AsyncMock()
    for k, v in overrides.items():
        setattr(fmt, k, v)
    return fmt


async def _gen(*items: RenderEvent) -> AsyncIterator[RenderEvent]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Test 1 — multi-tool turn drives edit_tool_recap streaming + done
# ---------------------------------------------------------------------------


async def test_multi_tool_turn_invokes_edit_tool_recap_streaming_and_done() -> None:
    """A turn with tool calls must invoke edit_tool_recap with done=False
    then done=True.

    Asserts:
    - send_trace_placeholder called exactly once (tool activity present).
    - edit_tool_recap called >=1 time with done=False.
    - edit_tool_recap called exactly once with done=True.
    - The done=True call carries lines starting with the recap header.
    """
    fmt = _make_formatter()
    session = StreamingSession(fmt, outbound=None)

    await session.run(
        _gen(
            RunStartedRenderEvent(run_id="r1"),
            ToolCallStartRenderEvent(tool_call_id="t1", tool_name="edit"),
            ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"path": "src/foo.py"}'),
            ToolCallEndRenderEvent(tool_call_id="t1"),
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="done"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        )
    )

    # send_trace_placeholder must fire exactly once when tool events are present
    fmt.send_trace_placeholder.assert_called_once()

    # Collect all edit_tool_recap calls
    all_calls = fmt.edit_tool_recap.call_args_list
    assert len(all_calls) >= 1, "edit_tool_recap must be called at least once"

    # There must be at least one done=False call
    done_false_calls = [
        c
        for c in all_calls
        if c.kwargs.get("done") is False
        or (c.args and len(c.args) >= 3 and c.args[2] is False)
    ]
    assert len(done_false_calls) >= 1, (
        "edit_tool_recap must be called with done=False at least once"
    )

    # There must be exactly one done=True call
    done_true_calls = [
        c
        for c in all_calls
        if c.kwargs.get("done") is True
        or (c.args and len(c.args) >= 3 and c.args[2] is True)
    ]
    assert len(done_true_calls) == 1, (
        "edit_tool_recap must be called with done=True exactly once"
    )

    # The lines on the done=True call must start with the recap header
    done_true_call = done_true_calls[0]
    if done_true_call.kwargs.get("lines") is not None:
        lines = done_true_call.kwargs["lines"]
    else:
        # positional: edit_tool_recap(trace_obj, lines, done)
        lines = done_true_call.args[1]
    assert lines, "done=True call must pass non-empty lines"
    assert lines[0].startswith("\U0001f527 Done"), (
        f"First line must start with recap header, got: {lines[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — text-only turn never sends trace placeholder (SC9 regression)
# ---------------------------------------------------------------------------


async def test_text_only_turn_never_sends_trace_placeholder() -> None:
    """Pure text stream must never trigger send_trace_placeholder or edit_tool_recap."""
    fmt = _make_formatter()
    session = StreamingSession(fmt, outbound=None)

    await session.run(
        _gen(
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="hi"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="r1", outcome="success"),
        )
    )

    fmt.send_trace_placeholder.assert_not_called()
    fmt.edit_tool_recap.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — ungraceful end (no RunFinished) still fires done=True edit
# ---------------------------------------------------------------------------


async def test_ungraceful_end_still_fires_done_true_edit() -> None:
    """Stream that ends abruptly (no RunFinished) must still invoke
    edit_tool_recap with done=True.
    """

    async def _abrupt_gen() -> AsyncIterator[RenderEvent]:
        yield ToolCallStartRenderEvent(tool_call_id="t1", tool_name="bash")
        yield ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"command": "ls"}')
        yield ToolCallEndRenderEvent(tool_call_id="t1")
        # No RunFinished — iterator ends here

    fmt = _make_formatter()
    session = StreamingSession(fmt, outbound=None)
    await session.run(_abrupt_gen())

    all_calls = fmt.edit_tool_recap.call_args_list
    done_true_calls = [
        c
        for c in all_calls
        if c.kwargs.get("done") is True or (len(c.args) >= 3 and c.args[2] is True)
    ]
    assert len(done_true_calls) == 1, (
        f"Expected exactly 1 done=True call after abrupt end, "
        f"got {len(done_true_calls)}. All calls: {all_calls}"
    )


# ---------------------------------------------------------------------------
# Test 4 — edit_tool_recap on formatter has a default no-op via Protocol
# ---------------------------------------------------------------------------


async def test_edit_tool_recap_on_formatter_is_callable() -> None:
    """OutboundFormatter.edit_tool_recap is callable on a real formatter impl.

    Uses TelegramFormatter to verify the Protocol method exists and is awaitable.
    """
    from unittest.mock import MagicMock

    from factory.adapters.telegram.telegram_formatter import TelegramFormatter

    adapter_mock = MagicMock()
    adapter_mock.bot = MagicMock()
    adapter_mock.bot.send_message = AsyncMock()
    adapter_mock.bot.edit_message_text = AsyncMock()

    formatter = TelegramFormatter(
        adapter_mock,
        chat_id=123,
        get_msg=lambda k, fb: fb,
        placeholder_text="…",
    )

    # The method must exist and be awaitable with no-op (lines=[], done=True)
    result = await formatter.edit_tool_recap(None, [], True)
    assert result is None


# ---------------------------------------------------------------------------
# Test 5 — orphan tool_call_id does not crash (defensive)
# ---------------------------------------------------------------------------


async def test_orphan_tool_call_id_does_not_crash() -> None:
    """ToolCallEnd with no matching Start must be silently ignored —
    session must not raise.
    """
    fmt = _make_formatter()
    session = StreamingSession(fmt, outbound=None)

    # No ToolCallStart for "ghost" — just an End
    await session.run(
        _gen(
            ToolCallEndRenderEvent(tool_call_id="ghost"),
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="ok"),
            TextEndRenderEvent(message_id="msg-1"),
        )
    )

    # Session must complete without raising
    fmt.edit_placeholder_text.assert_called()


# ---------------------------------------------------------------------------
# Test 6 — placeholder send failure: edit_tool_recap MUST NOT fire (#1220 review)
# ---------------------------------------------------------------------------


async def test_placeholder_send_failure_does_not_invoke_edit_tool_recap() -> None:
    """When send_trace_placeholder raises, _ensure_trace_obj returns False,
    _on_toolcall_v2 bails out, and _deliver_final's recap edit is skipped
    because self._trace_obj remains None.

    Regression guard for the failure path of _ensure_trace_obj — without
    this test, removing either the early-return in _on_toolcall_v2 or the
    None-guard in _deliver_final would not be caught by any assertion.
    """
    fmt = _make_formatter(
        send_trace_placeholder=AsyncMock(side_effect=RuntimeError("boom")),
    )
    session = StreamingSession(fmt, outbound=None)

    await session.run(
        _gen(
            RunStartedRenderEvent(run_id="run-fail"),
            ToolCallStartRenderEvent(tool_call_id="t1", tool_name="bash"),
            ToolCallArgsRenderEvent(tool_call_id="t1", delta='{"command":"ls"}'),
            ToolCallEndRenderEvent(tool_call_id="t1"),
            TextStartRenderEvent(message_id="msg-1"),
            TextDeltaRenderEvent(message_id="msg-1", delta="ok"),
            TextEndRenderEvent(message_id="msg-1"),
            RunFinishedRenderEvent(run_id="run-fail", outcome="success"),
        )
    )

    # Placeholder send was attempted exactly once.
    assert fmt.send_trace_placeholder.await_count == 1
    # No recap edit ever fired — neither streaming nor done=True.
    fmt.edit_tool_recap.assert_not_called()
