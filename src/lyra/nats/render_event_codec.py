"""NatsRenderEventCodec — explicit encoder/decoder for NATS streaming events.

Both NatsChannelProxy (hub, encodes) and NatsOutboundListener (adapter, decodes)
import from this single class.  Adding a new RenderEvent subtype requires one
change here — there is no way to silently drop it on the other side.
"""

from __future__ import annotations

import json
import logging
from typing import assert_never

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
    SCHEMA_VERSION_TEXT_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_SUMMARY_RENDER_EVENT,
    FileEditSummary,
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    SilentCounts,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import deserialize, serialize
from roxabi_nats._version_check import check_schema_version

log = logging.getLogger(__name__)


class NatsRenderEventCodec:
    """Encode/decode pair for RenderEvent ↔ NATS chunk payload.

    Wire format per chunk::

        {
            "stream_id": str,
            "seq":        int,
            "event_type": "text" | "text_start" | "text_delta"
                          | "text_end" | "text_chunk" | "tool_summary"
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
    ``decode()`` returns ``None`` for both. The
    ``run_*`` types were added by Slice 1 of #1096 (#1098).
    """

    def __init__(self, *, resolver: TypeHintResolver = TYPE_REGISTRY_RESOLVER) -> None:
        self._resolver = resolver

    @staticmethod
    def encode(event: RenderEvent) -> tuple[str, dict, bool]:  # noqa: C901 — DEBT:complexity-residual — explicit if-chain; refactored when Slice 5 sunsets v1
        """Return ``(event_type, payload_dict, is_done)`` for *event*.

        ``is_done`` is ``True`` for a final ``TextRenderEvent`` (``is_final``),
        a complete ``ToolSummaryRenderEvent`` (``is_complete``), or any of the
        terminal Run lifecycle events (``RunFinishedRenderEvent``,
        ``RunErrorRenderEvent``). ``RunStartedRenderEvent`` is not terminal.
        """
        payload: dict = json.loads(serialize(event).decode("utf-8"))
        if isinstance(event, TextRenderEvent):
            return "text", payload, event.is_final
        if isinstance(event, TextStartRenderEvent):
            return "text_start", payload, False
        if isinstance(event, TextDeltaRenderEvent):
            return "text_delta", payload, False
        if isinstance(event, TextEndRenderEvent):
            return "text_end", payload, False
        if isinstance(event, TextChunkRenderEvent):
            return "text_chunk", payload, False
        if isinstance(event, ToolSummaryRenderEvent):
            return "tool_summary", payload, event.is_complete
        if isinstance(event, RunStartedRenderEvent):
            return "run_started", payload, False
        if isinstance(event, RunFinishedRenderEvent):
            return "run_finished", payload, True
        if isinstance(event, RunErrorRenderEvent):
            return "run_error", payload, True
        if isinstance(event, ToolCallStartRenderEvent):
            return "tool_call_start", payload, False
        if isinstance(event, ToolCallArgsRenderEvent):
            return "tool_call_args", payload, False
        if isinstance(event, ToolCallEndRenderEvent):
            return "tool_call_end", payload, False
        if isinstance(event, ToolCallResultRenderEvent):
            return "tool_call_result", payload, False
        if isinstance(event, ReasoningStartRenderEvent):
            return "reasoning_start", payload, False
        if isinstance(event, ReasoningDeltaRenderEvent):
            return "reasoning_delta", payload, False
        if isinstance(event, ReasoningEndRenderEvent):  # pyright: ignore[reportUnnecessaryIsInstance] — last branch is provably exhaustive; isinstance kept for runtime symmetry with the others before assert_never
            return "reasoning_end", payload, False
        assert_never(event)

    def decode(  # noqa: C901 — DEBT:complexity-residual — per-event-type version-check + decode; refactored when Slice 5 sunsets v1
        self,
        event_type: str,
        payload: dict,
        *,
        counter: dict[str, int] | None = None,
    ) -> RenderEvent | None:
        """Reconstruct a ``RenderEvent`` from *(event_type, payload_dict)*.

        Returns ``None`` for synthetic types (``"stream_end"``), unknown event
        types, or payloads that fail the schema version check.  Callers should
        skip yielding ``None`` values.

        Args:
            event_type: The ``"event_type"`` field from the wire chunk.
            payload:    The ``"payload"`` dict from the wire chunk.
            counter:    Caller-owned mutable dict; incremented at
                        ``counter[envelope_name]`` on every version-check drop.
                        Pass ``None`` to skip counting.
        """
        if event_type == "text":
            if not check_schema_version(
                payload,
                envelope_name="TextRenderEvent",
                expected=SCHEMA_VERSION_TEXT_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "text_start":
            if not check_schema_version(
                payload,
                envelope_name="TextStartRenderEvent",
                expected=SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextStartRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "text_delta":
            if not check_schema_version(
                payload,
                envelope_name="TextDeltaRenderEvent",
                expected=SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextDeltaRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "text_end":
            if not check_schema_version(
                payload,
                envelope_name="TextEndRenderEvent",
                expected=SCHEMA_VERSION_TEXT_END_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextEndRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "text_chunk":
            if not check_schema_version(
                payload,
                envelope_name="TextChunkRenderEvent",
                expected=SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextChunkRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "tool_summary":
            if not check_schema_version(
                payload,
                envelope_name="ToolSummaryRenderEvent",
                expected=SCHEMA_VERSION_TOOL_SUMMARY_RENDER_EVENT,
                counter=counter,
            ):
                return None
            files_raw = payload.get("files", {})
            silent_raw = payload.get("silent_counts", {})
            return ToolSummaryRenderEvent(
                files={p: FileEditSummary(**d) for p, d in files_raw.items()},
                bash_commands=payload.get("bash_commands", []),
                web_fetches=payload.get("web_fetches", []),
                agent_calls=payload.get("agent_calls", []),
                silent_counts=(
                    SilentCounts(**silent_raw)
                    if isinstance(silent_raw, dict)
                    else silent_raw
                ),
                is_complete=payload.get("is_complete", False),
            )
        if event_type == "run_started":
            if not check_schema_version(
                payload,
                envelope_name="RunStartedRenderEvent",
                expected=SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                RunStartedRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "run_finished":
            if not check_schema_version(
                payload,
                envelope_name="RunFinishedRenderEvent",
                expected=SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                RunFinishedRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "run_error":
            if not check_schema_version(
                payload,
                envelope_name="RunErrorRenderEvent",
                expected=SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                RunErrorRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "tool_call_start":
            if not check_schema_version(
                payload,
                envelope_name="ToolCallStartRenderEvent",
                expected=SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ToolCallStartRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "tool_call_args":
            if not check_schema_version(
                payload,
                envelope_name="ToolCallArgsRenderEvent",
                expected=SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ToolCallArgsRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "tool_call_end":
            if not check_schema_version(
                payload,
                envelope_name="ToolCallEndRenderEvent",
                expected=SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ToolCallEndRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "tool_call_result":
            if not check_schema_version(
                payload,
                envelope_name="ToolCallResultRenderEvent",
                expected=SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ToolCallResultRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "reasoning_start":
            if not check_schema_version(
                payload,
                envelope_name="ReasoningStartRenderEvent",
                expected=SCHEMA_VERSION_REASONING_START_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ReasoningStartRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "reasoning_delta":
            if not check_schema_version(
                payload,
                envelope_name="ReasoningDeltaRenderEvent",
                expected=SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ReasoningDeltaRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "reasoning_end":
            if not check_schema_version(
                payload,
                envelope_name="ReasoningEndRenderEvent",
                expected=SCHEMA_VERSION_REASONING_END_RENDER_EVENT,
                counter=counter,
            ):
                return None
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ReasoningEndRenderEvent,
                resolver=self._resolver,
            )
        if event_type == "stream_end":
            return None
        if event_type == "stream_error":
            return None
        # Unknown event_type — surface via log + counter so partial-deploy
        # mismatches are visible. Synthetic transport sentinels (stream_end /
        # stream_error) are handled above.
        log.warning(
            "NatsRenderEventCodec: unknown event_type=%r; dropping chunk",
            event_type,
        )
        if counter is not None:
            key = f"unknown:{event_type}"
            counter[key] = counter.get(key, 0) + 1
        return None

    @staticmethod
    def is_terminal(event_type: str) -> bool:
        """Return ``True`` when this chunk signals end-of-stream.

        Rules:

        * ``"stream_end"`` / ``"stream_error"`` — always terminal (explicit
          sentinels from hub / transport).
        * ``"run_finished"`` / ``"run_error"`` — always terminal (Slice 1 of
          #1096 moved the canonical run terminator off ``text``/``done`` so
          adapters always see Run lifecycle events before the loop exits).
        * ``"tool_summary"`` — never terminal; subsequent events follow.
        * ``"text"`` — never terminal; ``run_finished``/``run_error``
          arrives after the final ``TextRenderEvent``. ``stream_end`` is
          still published unconditionally as a backward-compat safety net
          for receivers that pre-date Slice 1.
        """
        return event_type in (
            "stream_end",
            "stream_error",
            "run_finished",
            "run_error",
        )
