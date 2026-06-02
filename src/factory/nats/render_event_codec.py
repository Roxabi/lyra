"""NatsRenderEventCodec — registry-driven encoder/decoder for NATS streaming events.

Both NatsChannelProxy (hub, encodes) and NatsOutboundListener (adapter, decodes)
import from this single class.  Adding a new RenderEvent subtype requires one
registry insertion in ``render_event_codec_registry.py`` — the completeness test
(TestRegistryCompleteness) fails loud at CI if the registry is missing a union
member.

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

import logging

from factory.core.messaging.render_events import RenderEvent
from factory.nats.render_event_codec_registry import (
    CodecBranch,
    build_codec_registry,
)
from factory.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats import TypeHintResolver
from roxabi_nats._version_check import check_schema_version

log = logging.getLogger(__name__)

# Synthetic terminals and non-terminal sentinels that are NOT in the RenderEvent union.
# decode() checks membership here BEFORE registry lookup so these never
# surface the "unknown event_type" warning on clean stream close.
#
# ``stream_keepalive`` is published by NatsChannelProxy.send_streaming during idle
# periods (e.g. long tool calls) to prevent the adapter's decode_stream_events
# per-chunk timeout from tripping (#687).  The decoder resets its per-chunk timer on
# receipt but does NOT yield a render event to the caller.
_SYNTHETIC_TERMINALS: frozenset[str] = frozenset({"stream_end", "stream_error"})
_SYNTHETIC_NON_TERMINALS: frozenset[str] = frozenset({"stream_keepalive"})


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
                          | "reasoning_end" | "stream_end" | "stream_error"
                          | "stream_keepalive",
            "payload":    dict,   # serialized event fields
            "done":       bool,
        }

    ``"stream_end"`` and ``"stream_error"`` are synthetic terminal sentinels
    (the latter emitted by the transport on mid-stream hub crash, #538);
    ``decode()`` returns ``None`` for both.
    ``"stream_keepalive"`` is a non-terminal sentinel published by
    ``NatsChannelProxy.send_streaming`` during idle periods (#687); ``decode()``
    also returns ``None`` — the adapter resets its per-chunk timer but does not
    yield a render event.
    ``is_done=True`` for ``RunFinishedRenderEvent`` and ``RunErrorRenderEvent``
    only; all other types yield ``is_done=False``.
    """

    def __init__(self, *, resolver: TypeHintResolver = TYPE_REGISTRY_RESOLVER) -> None:
        self._resolver = resolver

        self._registry: dict[type, CodecBranch] = build_codec_registry(resolver)

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
        # Synthetic sentinels — short-circuit BEFORE registry lookup so these
        # never emit the "unknown event_type" warning on clean stream close.
        # _SYNTHETIC_TERMINALS: stream_end, stream_error — terminal, caller stops loop.
        # _SYNTHETIC_NON_TERMINALS: stream_keepalive — non-terminal, caller continues.
        # Both return None so the caller skips yielding a render event.
        if event_type in _SYNTHETIC_TERMINALS or event_type in _SYNTHETIC_NON_TERMINALS:
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
