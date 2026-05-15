"""NatsRenderEventCodec — registry-driven encoder/decoder for NATS streaming events.

Both NatsChannelProxy (hub, encodes) and NatsOutboundListener (adapter, decodes)
import from this single class.  Adding a new RenderEvent subtype requires one
registry insertion — the completeness test (TestRegistryCompleteness) fails loud
at CI if the registry is missing a union member.

Registry shape
--------------
``_registry: dict[type, CodecBranch]`` — keyed by RenderEvent subtype class.
``_by_type_str: dict[str, CodecBranch]`` — inverse index for O(1) decode lookup.
Both maps are immutable after ``__init__``.

Synthetic terminals
-------------------
``stream_end`` and ``stream_error`` are NOT in the RenderEvent union and therefore
NOT in ``_registry``.  ``decode()`` checks ``_SYNTHETIC_TERMINALS`` BEFORE the
registry lookup and returns ``None`` for those event types (no spurious
"unknown event_type" warning on clean stream close).
"""

from __future__ import annotations

import json
import logging
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
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import deserialize, serialize
from roxabi_nats._version_check import check_schema_version

log = logging.getLogger(__name__)

# Synthetic terminal sentinels that are NOT in the RenderEvent union.
# decode() checks membership here BEFORE registry lookup so these never
# surface the "unknown event_type" warning on clean stream close.
_SYNTHETIC_TERMINALS: frozenset[str] = frozenset({"stream_end", "stream_error"})


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


class NatsRenderEventCodec:
    """Encode/decode pair for RenderEvent ↔ NATS chunk payload.

    Wire format per chunk::

        {
            "stream_id": str,
            "seq":        int,
            "event_type": "text_start" | "text_delta" | "text_end" | "text_chunk"
                          | "run_started" | "run_finished" | "run_error"
                          | "tool_call_start" | "tool_call_args"
                          | "tool_call_end" | "tool_call_result"
                          | "reasoning_start" | "reasoning_delta"
                          | "reasoning_end" | "stream_end" | "stream_error",
            "payload":    dict,   # serialized event fields
            "done":       bool,
        }

    ``"stream_end"`` and ``"stream_error"`` are synthetic terminal sentinels
    (the latter emitted by the transport on mid-stream hub crash, #538);
    ``decode()`` returns ``None`` for both.
    ``is_done=True`` for ``RunFinishedRenderEvent`` and ``RunErrorRenderEvent``
    only; all other types yield ``is_done=False``.
    """

    def __init__(self, *, resolver: TypeHintResolver = TYPE_REGISTRY_RESOLVER) -> None:
        self._resolver = resolver

        # fmt: off
        self._registry: dict[type, CodecBranch] = {
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

        # Inverse index: event_type string → CodecBranch.  Built once from
        # _registry so decode() is O(1) and the two maps stay in sync.
        self._by_type_str: dict[str, CodecBranch] = {
            branch.event_type: branch for branch in self._registry.values()
        }

        # Terminal event types derived from the registry.  Used by is_terminal()
        # so the set stays in sync with _registry — a new branch with
        # is_done_default=True is automatically recognized as terminal.
        self._terminal_types: frozenset[str] = frozenset(
            branch.event_type
            for branch in self._registry.values()
            if branch.is_done_default
        )

    def encode(self, event: RenderEvent) -> tuple[str, dict, bool]:
        """Return ``(event_type, payload_dict, is_done)`` for *event*.

        ``is_done`` is ``True`` only for terminal Run lifecycle events
        (``RunFinishedRenderEvent``, ``RunErrorRenderEvent``).
        All other registered types yield ``is_done=False`` — the
        stream terminator is the Run lifecycle event, not any text-block
        or tool-call boundary.
        """
        branch = self._registry.get(type(event))
        if branch is None:
            raise TypeError(
                f"NatsRenderEventCodec.encode: unregistered RenderEvent type"
                f" {type(event).__name__!r} — add it to _registry"
            )
        return branch.encode_fn(event)

    def decode(
        self,
        event_type: str,
        payload: dict,
        *,
        counter: dict[str, int] | None = None,
    ) -> RenderEvent | None:
        """Reconstruct a ``RenderEvent`` from *(event_type, payload_dict)*.

        Returns ``None`` for synthetic terminals (``"stream_end"``,
        ``"stream_error"``), unknown event types, or payloads that fail the
        schema version check or raise a decode exception.  Callers should skip
        yielding ``None`` values.

        Args:
            event_type: The ``"event_type"`` field from the wire chunk.
            payload:    The ``"payload"`` dict from the wire chunk.
            counter:    Caller-owned mutable dict; incremented at
                        ``counter[envelope_name]`` on every version-check drop.
                        Pass ``None`` to skip counting.
        """
        # Synthetic terminals — short-circuit BEFORE registry lookup so these
        # never emit the "unknown event_type" warning on clean stream close.
        if event_type in _SYNTHETIC_TERMINALS:
            return None

        branch = self._by_type_str.get(event_type)
        if branch is None:
            log.warning(
                "NatsRenderEventCodec: unknown event_type=%r; dropping chunk",
                event_type,
            )
            if counter is not None:
                key = f"unknown:{event_type}"
                counter[key] = counter.get(key, 0) + 1
            return None

        # Schema version gate — per-branch expected version.
        # branch.cls_name carries the class name (e.g. "TextRenderEvent") so
        # counter keys match the existing convention ("TextRenderEvent:schema").
        if not check_schema_version(
            payload,
            envelope_name=branch.cls_name,
            expected=branch.schema_version,
            counter=counter,
        ):
            return None

        try:
            return branch.decode_fn(payload)
        except (TypeError, ValueError, KeyError, AttributeError):
            log.exception(
                "NatsRenderEventCodec: decode failed for event_type=%r; dropping chunk",
                event_type,
            )
            return None

    def is_terminal(self, event_type: str) -> bool:
        """Return ``True`` when this chunk signals end-of-stream.

        Rules:

        * ``"stream_end"`` / ``"stream_error"`` — always terminal (explicit
          sentinels from hub / transport; in ``_SYNTHETIC_TERMINALS``).
        * Any registered event type whose ``CodecBranch.is_done_default`` is
          ``True`` — currently ``"run_finished"`` and ``"run_error"``. The set
          is derived from ``_registry`` at construction time, so adding a new
          terminal type to the registry automatically extends ``is_terminal``.
        * All other registered event types — not terminal.
        * Unknown event types — not terminal.
        """
        return event_type in _SYNTHETIC_TERMINALS or event_type in self._terminal_types
