"""NatsRenderEventCodec registry — v2 event-type table.

Moved here from ``render_event_codec.py`` to keep the main file under the
300-line cap.  The registry maps every ``RenderEvent`` subtype to a
``CodecBranch`` (encode_fn, decode_fn, schema_version, is_done_default).

Adding a new RenderEvent subtype requires one insertion in
``build_codec_registry()`` — the completeness test (``TestRegistryCompleteness``)
fails loud at CI if the registry is missing a union member.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from lyra.core.messaging.render_events import (
    SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
    SCHEMA_VERSION_REASONING_END_RENDER_EVENT,
    SCHEMA_VERSION_REASONING_START_RENDER_EVENT,
    SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT,
    SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT,
    SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_END_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
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
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import deserialize, serialize


@dataclass(frozen=True)
class CodecBranch:
    """Per-event-type encode/decode descriptor stored in the registry.

    ``encode_fn`` maps a RenderEvent instance to ``(event_type, payload, is_done)``.
    ``is_done_default`` is ``True`` for terminal events (RunFinished, RunError)
    and ``False`` for all others.

    ``decode_fn`` maps a raw payload dict to a RenderEvent instance.  It is called
    inside a per-branch ``try/except`` in ``NatsRenderEventCodec.decode()``; it
    should raise TypeError / ValueError / KeyError / AttributeError on malformed
    input rather than returning a partially-constructed object.
    """

    event_type: str
    cls_name: str
    encode_fn: Callable[[RenderEvent], tuple[str, dict, bool]]
    decode_fn: Callable[[dict], RenderEvent]
    schema_version: int
    is_done_default: bool


def _make_std_encode(
    event_type: str, is_done: bool
) -> Callable[[RenderEvent], tuple[str, dict, bool]]:
    """Return an encode_fn that serialises via roxabi_nats with a fixed is_done."""

    def _encode(event: RenderEvent) -> tuple[str, dict, bool]:
        payload: dict = json.loads(serialize(event).decode("utf-8"))
        return event_type, payload, is_done

    return _encode


def _make_std_decode(
    cls: type, resolver: TypeHintResolver
) -> Callable[[dict], RenderEvent]:
    """Return a decode_fn that round-trips through roxabi_nats deserialize."""

    def _decode(payload: dict) -> RenderEvent:
        return deserialize(  # type: ignore[return-value]
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            cls,
            resolver=resolver,
        )

    return _decode


def build_codec_registry(
    resolver: TypeHintResolver,
) -> dict[type, CodecBranch]:
    """Return the immutable v2 registry keyed by RenderEvent subtype class."""

    # fmt: off
    registry: dict[type, CodecBranch] = {
        # v2 text triplet
        TextStartRenderEvent: CodecBranch(
            event_type="text_start",
            cls_name="TextStartRenderEvent",
            encode_fn=_make_std_encode("text_start", False),
            decode_fn=_make_std_decode(TextStartRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
            is_done_default=False,
        ),
        TextDeltaRenderEvent: CodecBranch(
            event_type="text_delta",
            cls_name="TextDeltaRenderEvent",
            encode_fn=_make_std_encode("text_delta", False),
            decode_fn=_make_std_decode(TextDeltaRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
            is_done_default=False,
        ),
        TextEndRenderEvent: CodecBranch(
            event_type="text_end",
            cls_name="TextEndRenderEvent",
            encode_fn=_make_std_encode("text_end", False),
            decode_fn=_make_std_decode(TextEndRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TEXT_END_RENDER_EVENT,
            is_done_default=False,
        ),
        TextChunkRenderEvent: CodecBranch(
            event_type="text_chunk",
            cls_name="TextChunkRenderEvent",
            encode_fn=_make_std_encode("text_chunk", False),
            decode_fn=_make_std_decode(TextChunkRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
            is_done_default=False,
        ),
        # Run lifecycle
        RunStartedRenderEvent: CodecBranch(
            event_type="run_started",
            cls_name="RunStartedRenderEvent",
            encode_fn=_make_std_encode("run_started", False),
            decode_fn=_make_std_decode(RunStartedRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT,
            is_done_default=False,
        ),
        RunFinishedRenderEvent: CodecBranch(
            event_type="run_finished",
            cls_name="RunFinishedRenderEvent",
            encode_fn=_make_std_encode("run_finished", True),
            decode_fn=_make_std_decode(RunFinishedRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT,
            is_done_default=True,
        ),
        RunErrorRenderEvent: CodecBranch(
            event_type="run_error",
            cls_name="RunErrorRenderEvent",
            encode_fn=_make_std_encode("run_error", True),
            decode_fn=_make_std_decode(RunErrorRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT,
            is_done_default=True,
        ),
        # Tool-call lifecycle
        ToolCallStartRenderEvent: CodecBranch(
            event_type="tool_call_start",
            cls_name="ToolCallStartRenderEvent",
            encode_fn=_make_std_encode("tool_call_start", False),
            decode_fn=_make_std_decode(ToolCallStartRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
            is_done_default=False,
        ),
        ToolCallArgsRenderEvent: CodecBranch(
            event_type="tool_call_args",
            cls_name="ToolCallArgsRenderEvent",
            encode_fn=_make_std_encode("tool_call_args", False),
            decode_fn=_make_std_decode(ToolCallArgsRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
            is_done_default=False,
        ),
        ToolCallEndRenderEvent: CodecBranch(
            event_type="tool_call_end",
            cls_name="ToolCallEndRenderEvent",
            encode_fn=_make_std_encode("tool_call_end", False),
            decode_fn=_make_std_decode(ToolCallEndRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
            is_done_default=False,
        ),
        ToolCallResultRenderEvent: CodecBranch(
            event_type="tool_call_result",
            cls_name="ToolCallResultRenderEvent",
            encode_fn=_make_std_encode("tool_call_result", False),
            decode_fn=_make_std_decode(ToolCallResultRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
            is_done_default=False,
        ),
        # Reasoning lifecycle
        ReasoningStartRenderEvent: CodecBranch(
            event_type="reasoning_start",
            cls_name="ReasoningStartRenderEvent",
            encode_fn=_make_std_encode("reasoning_start", False),
            decode_fn=_make_std_decode(ReasoningStartRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_REASONING_START_RENDER_EVENT,
            is_done_default=False,
        ),
        ReasoningDeltaRenderEvent: CodecBranch(
            event_type="reasoning_delta",
            cls_name="ReasoningDeltaRenderEvent",
            encode_fn=_make_std_encode("reasoning_delta", False),
            decode_fn=_make_std_decode(ReasoningDeltaRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
            is_done_default=False,
        ),
        ReasoningEndRenderEvent: CodecBranch(
            event_type="reasoning_end",
            cls_name="ReasoningEndRenderEvent",
            encode_fn=_make_std_encode("reasoning_end", False),
            decode_fn=_make_std_decode(ReasoningEndRenderEvent, resolver),
            schema_version=SCHEMA_VERSION_REASONING_END_RENDER_EVENT,
            is_done_default=False,
        ),
    }
    # fmt: on
    return registry
