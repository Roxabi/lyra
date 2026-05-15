"""Wire-boundary integration test for outbound render-event streaming.

Slice 5b of #1096. Spins up a real ``nats-server`` (via the session-scoped
fixture in ``conftest.py``), publishes one instance of every member of
``typing.get_args(RenderEvent)`` through :class:`NatsChannelProxy.send_streaming`,
subscribes from a real NATS client, decodes via :class:`NatsRenderEventCodec`,
and asserts ``decoded == original`` for every member.

Catches the exact wire-boundary class of bug that the in-process Slice 2
codec unit tests missed (PR #1173 added ``TextStart/Delta/End/Chunk`` to the
``RenderEvent`` union but not to the codec's ``encode()`` branches → prod
incident "no display text" → hotfix PR #1190). The completeness check below
fails closed when a new ``RenderEvent`` subclass is added without a sample —
the ``typing.get_args(RenderEvent)`` enumeration is the same trick the
upcoming registry-completeness test (#1102a) uses on the Python-side
dispatch.
"""

from __future__ import annotations

import asyncio
import json
import typing
from datetime import datetime, timezone

import pytest
from nats.aio.client import Client as NATS

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, Platform
from lyra.core.messaging.render_events import (
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
from lyra.nats.nats_channel_proxy import NatsChannelProxy
from lyra.nats.render_event_codec import NatsRenderEventCodec
from tests.nats.conftest import requires_nats_server

pytestmark = [requires_nats_server]
# asyncio mode is set to ``auto`` in pyproject.toml, so async tests get the
# asyncio marker automatically — applying it globally would falsely tag the
# sync completeness test below.


# ---------------------------------------------------------------------------
# Sample table — one realistic instance per RenderEvent subclass.
# ---------------------------------------------------------------------------
# Adding a new member to ``RenderEvent`` requires adding a sample here.
# ``test_sample_table_matches_render_event_union`` is the single source of
# fail-closed enforcement — it runs at collection time and prevents the
# parametrized round-trip below from being reached for an unsampled member.

_SAMPLE_BY_TYPE: dict[type, RenderEvent] = {
    TextStartRenderEvent: TextStartRenderEvent(message_id="msg-1"),
    TextDeltaRenderEvent: TextDeltaRenderEvent(message_id="msg-1", delta="he"),
    TextEndRenderEvent: TextEndRenderEvent(message_id="msg-1"),
    TextChunkRenderEvent: TextChunkRenderEvent(message_id="msg-1", delta="hi"),
    RunStartedRenderEvent: RunStartedRenderEvent(run_id="run-1"),
    RunFinishedRenderEvent: RunFinishedRenderEvent(run_id="run-1", outcome="success"),
    RunErrorRenderEvent: RunErrorRenderEvent(run_id="run-1", message="boom", code=None),
    ToolCallStartRenderEvent: ToolCallStartRenderEvent(
        tool_call_id="tc-1", tool_name="Read"
    ),
    ToolCallArgsRenderEvent: ToolCallArgsRenderEvent(
        tool_call_id="tc-1", delta='{"path": "x"}'
    ),
    ToolCallEndRenderEvent: ToolCallEndRenderEvent(tool_call_id="tc-1"),
    ToolCallResultRenderEvent: ToolCallResultRenderEvent(
        tool_call_id="tc-1", content="42", is_error=False
    ),
    ReasoningStartRenderEvent: ReasoningStartRenderEvent(message_id="r-1"),
    ReasoningDeltaRenderEvent: ReasoningDeltaRenderEvent(
        message_id="r-1", delta="thinking..."
    ),
    ReasoningEndRenderEvent: ReasoningEndRenderEvent(message_id="r-1"),
}


_UNION_MEMBERS: tuple[type, ...] = typing.get_args(RenderEvent)


# ---------------------------------------------------------------------------
# Completeness guard
# ---------------------------------------------------------------------------


def test_sample_table_matches_render_event_union() -> None:
    """Fail closed when ``_SAMPLE_BY_TYPE`` drifts from ``RenderEvent``.

    A new subclass added to the union without a sample here would otherwise
    silently bypass the round-trip check — exactly the failure mode that
    let PR #1173 ship without exercising the new TextStart/Delta/End/Chunk
    encode branches.
    """
    assert _UNION_MEMBERS, (
        "typing.get_args(RenderEvent) returned no members — the parametrized "
        "round-trip would pass vacuously"
    )
    sample_keys = set(_SAMPLE_BY_TYPE.keys())
    union = set(_UNION_MEMBERS)
    missing = sorted(t.__name__ for t in union - sample_keys)
    extra = sorted(t.__name__ for t in sample_keys - union)
    assert not missing, (
        f"Missing wire-round-trip samples for new RenderEvent subclasses: "
        f"{missing}. Add an instance to _SAMPLE_BY_TYPE in {__file__}."
    )
    assert not extra, (
        f"Stale wire-round-trip samples (no longer in RenderEvent union): "
        f"{extra}. Remove from _SAMPLE_BY_TYPE in {__file__}."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inbound(msg_id: str) -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        platform=Platform.TELEGRAM.value,
        bot_id="main",
        scope_id="chat:123",
        user_id="user:1",
        user_name="Alice",
        is_mention=False,
        text="ping",
        text_raw="ping",
        trust_level=TrustLevel.PUBLIC,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Round-trip — parametrized over every RenderEvent subclass.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    list(_UNION_MEMBERS),
    ids=lambda t: t.__name__,
)
async def test_render_event_wire_round_trip(event_type: type, nc: NATS) -> None:
    """One instance of ``event_type`` survives hub → NATS → adapter unchanged.

    Publish side: real :class:`NatsChannelProxy.send_streaming` against a real
    NATS connection — exercises the codec encoder, the JSON wire envelope,
    and the always-trailing ``stream_end`` sentinel.

    Receive side: a separate subscription on the same connection collects the
    raw JSON chunks, then :class:`NatsRenderEventCodec.decode` reconstructs
    the event. The decoded value must equal the original — anything less
    (missing branch, dropped field, schema-version mismatch) fails here.

    Completeness is enforced at collection time by
    ``test_sample_table_matches_render_event_union`` — a parametrized member
    without a sample never reaches this body.
    """
    original = _SAMPLE_BY_TYPE[event_type]
    inbound = _make_inbound(f"stream-{event_type.__name__}")
    subject = f"lyra.outbound.{Platform.TELEGRAM.value}.main"

    received: list[dict] = []
    parse_errors: list[bytes] = []
    done = asyncio.Event()

    async def _handler(msg) -> None:
        # Surface decode errors via ``parse_errors`` so the post-loop assertion
        # fails with the offending bytes rather than letting the exception
        # propagate into the NATS dispatch loop (where it would silently drop
        # subsequent messages and the test would only fail via the 5 s timeout).
        try:
            chunk = json.loads(msg.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parse_errors.append(msg.data)
            done.set()
            return
        received.append(chunk)
        if chunk.get("event_type") == "stream_end":
            done.set()

    sub = await nc.subscribe(subject, cb=_handler)
    # Flush so the SUB is registered with the server before we publish —
    # otherwise the first PUB races the subscription and the message is lost.
    await nc.flush()

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")

    async def _events():
        yield original

    try:
        await proxy.send_streaming(inbound, _events(), outbound=None)
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        await sub.unsubscribe()

    assert not parse_errors, (
        f"malformed NATS frame(s) received for {event_type.__name__}: {parse_errors!r}"
    )
    # send_streaming always appends a synthetic stream_end terminator, so we
    # expect exactly two chunks: the event + the sentinel.
    assert len(received) == 2, (
        f"expected 1 event chunk + 1 stream_end, got {len(received)}: {received!r}"
    )
    event_chunk, terminator = received
    assert terminator["event_type"] == "stream_end"
    assert terminator["stream_id"] == inbound.id
    assert event_chunk["stream_id"] == inbound.id
    assert event_chunk["seq"] == 0

    codec = NatsRenderEventCodec()
    decoded = codec.decode(event_chunk["event_type"], event_chunk["payload"])
    assert decoded is not None, (
        "decoder returned None — likely a schema-version mismatch or an "
        f"unknown event_type for {event_type.__name__} "
        f"(event_type={event_chunk['event_type']!r})"
    )
    assert decoded == original, (
        f"wire round-trip mismatch for {event_type.__name__}: "
        f"expected {original!r}, got {decoded!r}"
    )
