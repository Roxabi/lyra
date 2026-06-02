"""Emitter run-loop extraction — free functions operating on an OutboundEmitter.

Extracted from OutboundEmitter (issue #1591, stage-axis refactor).
These functions accept the emitter instance as a parameter and access its
internal state directly, keeping the class API surface minimal.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, assert_never

from factory.core.messaging import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from factory.core.messaging.tool_display_config import ToolDisplayConfig
from factory.outbound._placeholder_lifecycle import (
    _deliver_final,
    _drain_fallback,
    _handle_typing_tail,
    _send_placeholder,
)
from factory.outbound._tool_recap import ToolRecapAccumulator

if TYPE_CHECKING:
    from factory.outbound.emitter import OutboundEmitter


async def _prepend(
    first: RenderEvent, rest: AsyncIterator[RenderEvent]
) -> AsyncIterator[RenderEvent]:
    """Yield *first*, then all items from *rest*."""
    yield first
    async for ev in rest:
        yield ev


async def _run_event_loop(  # noqa: C901
    emitter: "OutboundEmitter",
    events: AsyncIterator[RenderEvent],
    placeholder_obj: Any,
) -> None:
    """Iterate over events, updating the placeholder with debounced edits."""
    try:
        async for event in events:
            if isinstance(
                event,
                RunStartedRenderEvent | RunFinishedRenderEvent | RunErrorRenderEvent,
            ):
                # Slice 1 (#1098): Run lifecycle events are pure additive
                # surface — adapters initially ignore (no UX). Future slices
                # may render banners or expose run_id in observability.
                # RunErrorRenderEvent flags the turn as error so the
                # subsequent TextEnd (if any) sets is_error_turn=True,
                # producing the ``❌`` prefix on the final rendered text.
                if isinstance(event, RunErrorRenderEvent):
                    emitter._st.is_error_pending = True
                continue
            if isinstance(
                event,
                ToolCallStartRenderEvent
                | ToolCallArgsRenderEvent
                | ToolCallEndRenderEvent
                | ToolCallResultRenderEvent,
            ):
                # Slice 3 (#1100) / Slice 5 (#1192): ToolCall* lifecycle
                # events. v1 ToolSummaryRenderEvent removed; platform
                # subclasses override _on_toolcall_v2 for richer rendering.
                await emitter._on_toolcall_v2(event)
                continue
            if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                event,
                TextStartRenderEvent
                | TextDeltaRenderEvent
                | TextEndRenderEvent
                | TextChunkRenderEvent,
            ):
                await emitter._on_text_v2(event, placeholder_obj)
                continue
            if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
                event,
                ReasoningStartRenderEvent
                | ReasoningDeltaRenderEvent
                | ReasoningEndRenderEvent,
            ):
                # Slice 4 (#1101): typed reasoning events. Routed through
                # OutboundFormatter.edit_reasoning. On Start, ensure the
                # shared trace placeholder exists so reasoning and recap
                # share a single placeholder object.
                if isinstance(event, ReasoningStartRenderEvent):
                    await emitter._ensure_trace_obj()
                await emitter._fmt.edit_reasoning(emitter._trace_obj, event)
                continue
            else:
                assert_never(event)
    except Exception as exc:  # noqa: BLE001 — terminal stream-error capture; broad-catch is intentional
        emitter._st.stream_error = exc


async def _run_emitter(
    emitter: "OutboundEmitter",
    events: AsyncIterator[RenderEvent],
) -> None:
    """Run the full streaming lifecycle.

    Defers the placeholder until the first event arrives so that
    backend failures never leave an orphaned "…".  Re-raises
    stream errors after delivering the error message.
    """
    config = emitter.tool_display_config or ToolDisplayConfig()
    emitter._tool_recap = ToolRecapAccumulator(config=config)
    # Peek: empty stream → fallback, no placeholder.
    first_event: RenderEvent | None = None
    peek_error: Exception | None = None
    try:
        try:
            first_event = await events.__anext__()
        except StopAsyncIteration:
            pass
        except Exception as exc:  # noqa: BLE001 — terminal stream-error capture; broad-catch is intentional
            peek_error = exc
        if first_event is None and peek_error is None:
            await _drain_fallback(emitter, events)
            await _handle_typing_tail(emitter)
            return
        if peek_error is not None:
            emitter._st.stream_error = peek_error
            result = await _send_placeholder(emitter)
            if result is not None:
                await _deliver_final(emitter, result[0])
            await _handle_typing_tail(emitter)
            raise peek_error
        assert first_event is not None  # narrowed above
        result = await _send_placeholder(emitter)
        full = _prepend(first_event, events)
        if result is None:
            await _drain_fallback(emitter, full)
            await _handle_typing_tail(emitter)
            return
        placeholder_obj, _ = result
        await _run_event_loop(emitter, full, placeholder_obj)
        await _deliver_final(emitter, placeholder_obj)
        await _handle_typing_tail(emitter)
        if emitter._st.stream_error is not None:
            raise emitter._st.stream_error
    finally:
        if isinstance(events, AsyncGenerator):
            await events.aclose()
