"""NatsRenderEventCodec — explicit encoder/decoder for NATS streaming events.

Both NatsChannelProxy (hub, encodes) and NatsOutboundListener (adapter, decodes)
import from this single class.  Adding a new RenderEvent subtype requires one
change here — there is no way to silently drop it on the other side.
"""
from __future__ import annotations

import json

from lyra.core.render_events import (
    FileEditSummary,
    RenderEvent,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.nats._serialize import deserialize, serialize


class NatsRenderEventCodec:
    """Encode/decode pair for RenderEvent ↔ NATS chunk payload.

    All methods are static — instantiation is not required.

    Wire format per chunk::

        {
            "stream_id": str,
            "seq":        int,
            "event_type": "text" | "tool_summary" | "stream_end",
            "payload":    dict,   # serialized event fields
            "done":       bool,
        }

    ``"stream_end"`` is a synthetic terminal sentinel emitted by
    ``NatsChannelProxy``; ``decode()`` returns ``None`` for it.
    """

    @staticmethod
    def encode(event: RenderEvent) -> tuple[str, dict, bool]:
        """Return ``(event_type, payload_dict, is_done)`` for *event*.

        ``is_done`` is ``True`` for a final ``TextRenderEvent`` (``is_final``)
        or a complete ``ToolSummaryRenderEvent`` (``is_complete``).
        """
        payload: dict = json.loads(serialize(event).decode("utf-8"))
        if isinstance(event, TextRenderEvent):
            return "text", payload, event.is_final
        # ToolSummaryRenderEvent
        return "tool_summary", payload, event.is_complete

    @staticmethod
    def decode(event_type: str, payload: dict) -> RenderEvent | None:
        """Reconstruct a ``RenderEvent`` from *(event_type, payload_dict)*.

        Returns ``None`` for synthetic types (``"stream_end"``) or unknown
        event types — callers should skip yielding ``None`` values.
        """
        if event_type == "text":
            return deserialize(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                TextRenderEvent,
            )
        if event_type == "tool_summary":
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
        return None  # "stream_end" or unknown

    @staticmethod
    def is_terminal(event_type: str, is_done: bool) -> bool:
        """Return ``True`` when this chunk signals end-of-stream.

        Rules:

        * ``"stream_end"`` — always terminal (explicit sentinel from hub).
        * ``"tool_summary"`` — never terminal; the final ``TextRenderEvent``
          follows unconditionally.
        * All other types (including ``"text"``) — terminal when ``is_done``
          is ``True``.
        """
        if event_type in ("stream_end", "stream_error"):
            return True
        if event_type == "tool_summary":
            return False
        return is_done
