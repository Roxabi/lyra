"""Tests for NatsChannelProxy — ChannelAdapter over NATS.

Uses unittest.mock.AsyncMock for the NATS client so no real NATS server is
needed. Each test verifies subject routing and envelope structure independently.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.messaging.render_events import (
    RunFinishedRenderEvent,
    TextDeltaRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.nats.nats_channel_proxy import NatsChannelProxy
from tests.helpers.messages import make_test_blobref

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc() -> MagicMock:
    """Return a mock NATS client with async publish and a JetStream context mock."""
    nc = MagicMock()
    nc.publish = AsyncMock()
    js = MagicMock()
    js.publish = AsyncMock(return_value=MagicMock())  # PubAck stub
    nc.jetstream = MagicMock(return_value=js)
    return nc


def _make_inbound(msg_id: str = "msg-1") -> InboundMessage:
    """Minimal valid InboundMessage for use in tests."""
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="user-42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


async def _async_iter(*items):
    """Yield items from an async iterator."""
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_init_stores_attributes() -> None:
    """NatsChannelProxy stores nc, platform, and bot_id without I/O."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    assert proxy._nc is nc
    assert proxy._platform is Platform.TELEGRAM
    assert proxy._bot_id == "main"


# ---------------------------------------------------------------------------
# normalize — must raise NotImplementedError
# ---------------------------------------------------------------------------


def test_normalize_raises() -> None:
    """normalize() raises NotImplementedError — proxy does not handle inbound."""
    proxy = NatsChannelProxy(nc=_make_nc(), platform=Platform.TELEGRAM, bot_id="main")
    with pytest.raises(
        NotImplementedError, match="does not normalize inbound messages"
    ):
        proxy.normalize({})


def test_normalize_audio_raises() -> None:
    """normalize_audio() raises NotImplementedError — proxy does not handle inbound."""
    proxy = NatsChannelProxy(nc=_make_nc(), platform=Platform.TELEGRAM, bot_id="main")
    with pytest.raises(NotImplementedError, match="does not normalize audio messages"):
        proxy.normalize_audio({}, b"bytes", "audio/ogg", trust_level=TrustLevel.TRUSTED)


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_publishes_to_correct_subject() -> None:
    """send() publishes to lyra.outbound.<platform>.<bot_id>."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-abc")
    outbound = OutboundMessage.from_text("Hi there")

    await proxy.send(inbound, outbound)

    nc.publish.assert_awaited_once()
    _subject, _payload = nc.publish.call_args.args
    assert _subject == "lyra.outbound.telegram.main"


@pytest.mark.asyncio
async def test_send_envelope_structure() -> None:
    """send() envelope has type=send, stream_id, and outbound fields."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.DISCORD, bot_id="bot2")
    inbound = _make_inbound("msg-xyz")
    outbound = OutboundMessage.from_text("Response text")

    await proxy.send(inbound, outbound)

    _subject, payload = nc.publish.call_args.args
    envelope = json.loads(payload.decode("utf-8"))

    assert envelope["type"] == "send"
    assert envelope["stream_id"] == "msg-xyz"
    assert "msg_id" not in envelope
    assert "outbound" in envelope
    assert isinstance(envelope["outbound"], dict)
    # Verify outbound content was serialized
    assert envelope["outbound"]["content"] == ["Response text"]


@pytest.mark.asyncio
async def test_send_subject_uses_platform_value() -> None:
    """send() uses Platform.value (string) in subject, not enum name."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.DISCORD, bot_id="main")
    await proxy.send(_make_inbound(), OutboundMessage.from_text("x"))

    subject, _ = nc.publish.call_args.args
    assert "discord" in subject
    assert "DISCORD" not in subject


# ---------------------------------------------------------------------------
# send_streaming()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_streaming_publishes_chunks_with_incrementing_seq() -> None:
    """send_streaming() assigns seq numbers starting at 0, monotonic +1 per chunk."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-stream")

    # Two v2 events: a tool-call start followed by a run-finished terminal
    event0 = ToolCallStartRenderEvent(tool_call_id="tc-1", tool_name="bash")
    event1 = RunFinishedRenderEvent(run_id="run-1")

    await proxy.send_streaming(inbound, _async_iter(event0, event1))

    # 2 event chunks + 1 terminal sentinel = 3 publishes
    assert nc.publish.await_count == 3
    calls = nc.publish.call_args_list

    chunk0 = json.loads(calls[0].args[1].decode("utf-8"))
    chunk1 = json.loads(calls[1].args[1].decode("utf-8"))
    sentinel = json.loads(calls[2].args[1].decode("utf-8"))

    assert chunk0["seq"] == 0
    assert chunk1["seq"] == 1
    # sentinel is seq-numbered in the same monotone series as event chunks
    assert sentinel["seq"] == 2


@pytest.mark.asyncio
async def test_send_streaming_subject_is_single_outbound_subject() -> None:
    """Every nc.publish call uses subject lyra.outbound.<platform>.<bot_id>."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-42")

    await proxy.send_streaming(
        inbound,
        _async_iter(
            TextDeltaRenderEvent(message_id="msg-42", delta="Hi"),
            RunFinishedRenderEvent(run_id="run-42"),
        ),
    )

    expected = "lyra.outbound.telegram.main"
    for call in nc.publish.call_args_list:
        subject, _ = call.args
        assert subject == expected


@pytest.mark.asyncio
async def test_send_streaming_sentinel_done_true() -> None:
    """Terminal sentinel (stream_end) has done=True; RunFinished chunk also done=True.

    Two assertions:
    1. The encoded RunFinishedRenderEvent chunk (first publish) carries done=True
       because the codec marks RunFinished with is_done=True.
    2. The synthetic stream_end sentinel (last publish) also carries done=True.
    """
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound, _async_iter(RunFinishedRenderEvent(run_id="run-done"))
    )

    # First publish: the RunFinishedRenderEvent chunk — codec marks it done=True
    _, chunk_payload = nc.publish.call_args_list[0].args
    chunk = json.loads(chunk_payload.decode("utf-8"))
    assert chunk["done"] is True

    # Second publish: the stream_end sentinel (explicit index, not -1 shorthand).
    assert nc.publish.await_count == 2
    _, payload = nc.publish.call_args_list[1].args
    sentinel = json.loads(payload.decode("utf-8"))
    assert sentinel["event_type"] == "stream_end"
    assert sentinel["done"] is True


@pytest.mark.asyncio
async def test_send_streaming_done_false_on_non_final_event() -> None:
    """Mid-stream chunk envelopes (TextDelta) have done=False."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound,
        _async_iter(TextDeltaRenderEvent(message_id="m1", delta="Partial")),
    )

    # call_args_list[0] is the event chunk; last call is the terminal sentinel
    _, payload = nc.publish.call_args_list[0].args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["event_type"] == "text_delta"
    assert chunk["done"] is False


@pytest.mark.asyncio
async def test_send_streaming_event_type_text() -> None:
    """send_streaming() sets event_type='text_delta' for TextDeltaRenderEvent."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound,
        _async_iter(TextDeltaRenderEvent(message_id="m1", delta="Hello")),
    )

    _, payload = nc.publish.call_args_list[0].args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["event_type"] == "text_delta"


@pytest.mark.asyncio
async def test_send_streaming_event_type_tool_call_start() -> None:
    """send_streaming(): ToolCallStart yields v2 event_type='tool_call_start'."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound,
        _async_iter(ToolCallStartRenderEvent(tool_call_id="tc-1", tool_name="bash")),
    )

    _, payload = nc.publish.call_args_list[0].args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["event_type"] == "tool_call_start"
    assert chunk["done"] is False


@pytest.mark.asyncio
async def test_send_streaming_chunk_has_stream_id_no_type() -> None:
    """Each chunk envelope has stream_id + event_type and NO legacy 'type' key."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-check")

    await proxy.send_streaming(
        inbound,
        _async_iter(TextDeltaRenderEvent(message_id="msg-check", delta="x")),
    )

    # Inspect every published envelope (event chunk + terminal sentinel)
    for call in nc.publish.call_args_list:
        _, raw = call.args
        envelope = json.loads(raw.decode("utf-8"))
        # All envelopes carry stream_id and event_type
        assert envelope["stream_id"] == "msg-check"
        assert "event_type" in envelope
        assert "msg_id" not in envelope
        assert "payload" in envelope
        # Regular event chunks and the stream_end sentinel must NOT have 'type'
        # (only the stream_error recovery envelope uses 'type')
        assert "type" not in envelope


@pytest.mark.asyncio
async def test_send_streaming_drains_iterator_on_publish_failure() -> None:
    """On NATS publish failure on first chunk, all remaining events are drained."""
    call_count = 0

    async def _publish_with_failure(_subject, _payload):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise nats.errors.Error("NATS connection lost")

    nc = _make_nc()
    nc.publish = AsyncMock(side_effect=_publish_with_failure)
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    yielded = []

    async def _events():
        for delta in ("first", "second", "third"):
            yielded.append(delta)
            yield TextDeltaRenderEvent(message_id="m1", delta=delta)

    # Should not raise; source iterator must be fully exhausted
    await proxy.send_streaming(inbound, _events())

    assert yielded == ["first", "second", "third"]
    # call 1: first chunk publish (raises); call 2: stream_error envelope
    # drain loop must NOT re-publish items 2+3 after the first chunk's publish failed
    assert nc.publish.await_count == 2


# ---------------------------------------------------------------------------
# render_attachment()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_attachment_publishes_to_outbound_subject() -> None:
    """render_attachment() publishes to lyra.outbound.<platform>.<bot_id>."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-att")
    attachment = OutboundAttachment(
        data=b"PNG",
        type="image",
        mime_type="image/png",
        filename="img.png",
    )

    await proxy.render_attachment(attachment, inbound)

    nc.publish.assert_awaited_once()
    subject, payload = nc.publish.call_args.args
    assert subject == "lyra.outbound.telegram.main"

    envelope = json.loads(payload.decode("utf-8"))
    assert envelope["type"] == "attachment"
    assert envelope["stream_id"] == "msg-att"
    assert "msg_id" not in envelope
    assert "attachment" in envelope
    assert isinstance(envelope["attachment"], dict)
    assert envelope["attachment"]["type"] == "image"
    assert envelope["attachment"]["mime_type"] == "image/png"


# ---------------------------------------------------------------------------
# render_audio() — warning, no publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_audio_publishes_to_nats() -> None:
    """render_audio() publishes a type=audio envelope via JetStream to durable subj."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-audio")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\x00\x01"), mime_type="audio/ogg"
    )

    await proxy.render_audio(audio, inbound)

    # Must use JetStream publish, not core NATS publish
    nc.publish.assert_not_awaited()
    js = nc.jetstream()
    js.publish.assert_awaited_once()
    subject, payload = js.publish.await_args.args
    assert subject == "lyra.outbound.audio.telegram.main"
    data = json.loads(payload)
    assert data["type"] == "audio"
    assert data["stream_id"] == "msg-audio"
    assert "audio" in data
    assert "original_msg" in data


# ---------------------------------------------------------------------------
# render_audio_publish — durable JetStream subject + Nats-Msg-Id header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_audio_publish_subject_and_header() -> None:
    """render_audio() uses 5-token audio subject and Nats-Msg-Id = stream_id.

    Asserts:
    - js.publish is called (not nc.publish)
    - subject is lyra.outbound.audio.<platform>.<bot_id>
    - Nats-Msg-Id header == inbound.id (stream_id)
    - PubAck is awaited (js.publish is awaited, not fire-and-forget)
    """
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="bot1")
    inbound = _make_inbound("stream-123")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\xff\xfe"), mime_type="audio/ogg"
    )

    await proxy.render_audio(audio, inbound)

    js = nc.jetstream()
    js.publish.assert_awaited_once()
    call = js.publish.await_args
    subj, _payload = call.args
    headers = call.kwargs.get("headers") or {}

    assert subj == "lyra.outbound.audio.telegram.bot1"
    assert headers.get("Nats-Msg-Id") == "stream-123"
    # nc.publish (core, at-most-once) must NOT be called
    nc.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# render_audio_puback_fail — publish failure → notif dispatched, no re-raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_audio_puback_fail_dispatches_notification() -> None:
    """On js.publish failure, a voice-undelivered notification is sent via nc.publish.

    Asserts:
    (a) notify_undelivered text is published to the legacy text subject
    (b) no raw exception text in the published notification payload
    (c) render_audio does not re-raise the publish error
    """
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=nats.errors.Error("stream unavailable"))

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-fail-42")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\xde\xad"), mime_type="audio/ogg"
    )

    # (c) must not raise
    await proxy.render_audio(audio, inbound)

    # (a) notification dispatched via legacy text subject
    nc.publish.assert_awaited_once()
    notif_subject, notif_payload_bytes = nc.publish.await_args.args
    assert notif_subject == "lyra.outbound.telegram.main"

    notif_data = json.loads(notif_payload_bytes)
    assert notif_data["type"] == "send"
    assert notif_data["stream_id"] == "msg-fail-42"
    outbound_content = notif_data["outbound"]["content"]
    # Notification text must contain user-facing message
    assert any("Voice" in part or "voice" in part for part in outbound_content)

    # (b) raw exception string must NOT appear in the published payload
    raw_payload_str = notif_payload_bytes.decode("utf-8")
    assert "stream unavailable" not in raw_payload_str
    assert "nats.errors" not in raw_payload_str


@pytest.mark.asyncio
async def test_render_audio_puback_fail_timeout_dispatches_notification() -> None:
    """asyncio.TimeoutError on PubAck also triggers the notification path."""
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=asyncio.TimeoutError())

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-timeout-7")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\xbe\xef"), mime_type="audio/ogg"
    )

    await proxy.render_audio(audio, inbound)

    nc.publish.assert_awaited_once()
    notif_subject, _ = nc.publish.await_args.args
    assert notif_subject == "lyra.outbound.telegram.main"


@pytest.mark.asyncio
async def test_render_audio_puback_fail_notif_publish_also_fails() -> None:
    """When both js.publish AND nc.publish fail, render_audio does not raise."""
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=nats.errors.Error("js down"))
    nc.publish = AsyncMock(side_effect=nats.errors.Error("nc down too"))

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-double-fail-99")
    audio = OutboundAudio(blob_ref=make_test_blobref(b"\x00"), mime_type="audio/ogg")

    # Must not raise even when the fallback notif publish also fails
    await proxy.render_audio(audio, inbound)


# ---------------------------------------------------------------------------
# render_audio_stream() — drain + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_audio_stream_drains_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """render_audio_stream() drains the iterator and logs a warning."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-astream")

    consumed = []

    async def _chunks():
        for i in range(3):
            chunk = OutboundAudioChunk(
                chunk_bytes=bytes([i]),
                session_id="s1",
                chunk_index=i,
                is_final=(i == 2),
            )
            consumed.append(i)
            yield chunk

    with caplog.at_level(logging.WARNING, logger="lyra.nats.nats_channel_proxy"):
        await proxy.render_audio_stream(_chunks(), inbound)

    nc.publish.assert_not_awaited()
    assert consumed == [0, 1, 2]
    assert any("audio-stream-over-NATS" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# render_voice_stream() — drain + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_voice_stream_drains_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """render_voice_stream() drains the iterator and logs a warning."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-vstream")

    consumed = []

    async def _chunks():
        for i in range(2):
            consumed.append(i)
            yield OutboundAudioChunk(
                chunk_bytes=bytes([i]),
                session_id="s2",
                chunk_index=i,
            )

    with caplog.at_level(logging.WARNING, logger="lyra.nats.nats_channel_proxy"):
        await proxy.render_voice_stream(_chunks(), inbound)

    nc.publish.assert_not_awaited()
    assert consumed == [0, 1]
    assert any("voice-stream-over-NATS" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# New API tests: stream_id / type=send / single subject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_includes_stream_id() -> None:
    """send() envelope uses stream_id (not msg_id) and type=send."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-stream-id-check")
    outbound = OutboundMessage.from_text("hi")

    await proxy.send(inbound, outbound)

    call_args = nc.publish.call_args
    envelope = json.loads(call_args.args[1])
    assert envelope["stream_id"] == inbound.id
    assert envelope["type"] == "send"
    assert "msg_id" not in envelope


# B7-8 (T7): deleted — duplicate of test_send_streaming_subject_is_single_outbound_subject  # noqa: E501


# ---------------------------------------------------------------------------
# is_terminal — stream_error event type
# ---------------------------------------------------------------------------


def test_is_terminal_stream_error():
    """stream_error event type is always terminal regardless of done flag."""
    from lyra.nats.render_event_codec import NatsRenderEventCodec

    codec = NatsRenderEventCodec()
    assert codec.is_terminal("stream_error") is True


# ---------------------------------------------------------------------------
# _active_streams tracking + publish_stream_errors (V2 — slice 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_streams_tracked_during_streaming() -> None:
    """_active_streams contains stream_id mid-flight; empty after send_streaming()."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-track")

    mid_flight_snapshot: set[str] = set()

    async def _events():
        yield TextDeltaRenderEvent(message_id="msg-track", delta="first")
        # Snapshot AFTER the first yield is consumed — by this point send_streaming()
        # has definitely called _active_streams.add(stream_id) and processed event 1.
        mid_flight_snapshot.update(proxy._active_streams)
        yield TextDeltaRenderEvent(message_id="msg-track", delta="second")

    await proxy.send_streaming(inbound, _events())

    # During emission the stream_id must have been present
    assert "msg-track" in mid_flight_snapshot
    # After completion the tracking set must be empty
    assert proxy._active_streams == set()


@pytest.mark.asyncio
async def test_publish_stream_errors_publishes_for_active() -> None:
    """publish_stream_errors() sends type=stream_error for each active stream_id."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")

    # Manually seed two active streams (as the implementation will maintain)
    proxy._active_streams = {"stream-a", "stream-b"}

    await proxy.publish_stream_errors("hub_shutdown")

    # One publish call per active stream_id
    assert nc.publish.await_count == 2

    subject = f"lyra.outbound.{proxy._platform.value}.{proxy._bot_id}"
    published_envelopes = []
    for call in nc.publish.call_args_list:
        call_subject, call_payload = call.args
        assert call_subject == subject
        published_envelopes.append(json.loads(call_payload.decode("utf-8")))

    stream_ids_published = {env["stream_id"] for env in published_envelopes}
    assert stream_ids_published == {"stream-a", "stream-b"}
    for env in published_envelopes:
        assert env["type"] == "stream_error"
        assert env["reason"] == "hub_shutdown"

    # _active_streams must be cleared afterwards
    assert proxy._active_streams == set()


@pytest.mark.asyncio
async def test_publish_stream_errors_swallows_nats_failure() -> None:
    """publish_stream_errors() does not raise even when nc.publish fails."""
    nc = _make_nc()
    nc.publish = AsyncMock(side_effect=nats.errors.Error("NATS gone"))
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")

    proxy._active_streams = {"stream-x", "stream-y"}

    # Must not raise despite publish failure
    await proxy.publish_stream_errors("hub_shutdown")

    # _active_streams must be cleared even on failure
    assert proxy._active_streams == set()


@pytest.mark.asyncio
async def test_publish_stream_errors_noop_when_no_active_streams() -> None:
    """publish_stream_errors() is a no-op when there are no active streams."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")

    await proxy.publish_stream_errors("hub_shutdown")

    assert nc.publish.await_count == 0
    assert proxy._active_streams == set()


@pytest.mark.asyncio
async def test_send_streaming_exception_publishes_stream_error() -> None:
    """On NATS publish failure mid-stream, a stream_error envelope is published."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-err-publish")

    call_count = 0

    async def _publish_with_failure(_subject, _payload):
        nonlocal call_count
        call_count += 1
        # Fail on the second chunk publish (outbound=None so no stream_start;
        # call 1 = chunk seq=0 succeeds, call 2 = chunk seq=1 raises)
        if call_count == 2:
            raise Exception("NATS down")

    nc.publish = AsyncMock(side_effect=_publish_with_failure)

    await proxy.send_streaming(
        inbound,
        _async_iter(
            TextDeltaRenderEvent(message_id="msg-err-publish", delta="a"),
            TextDeltaRenderEvent(message_id="msg-err-publish", delta="b"),
        ),
    )

    # Find the stream_error envelope in the publish calls
    stream_error_envelopes = []
    for call in nc.publish.call_args_list:
        payload = call.args[1]
        data = json.loads(payload.decode("utf-8"))
        if data.get("type") == "stream_error":
            stream_error_envelopes.append(data)

    assert len(stream_error_envelopes) == 1, (
        f"Expected 1 stream_error publish, got {len(stream_error_envelopes)}: "
        f"{stream_error_envelopes}"
    )
    err = stream_error_envelopes[0]
    assert err["stream_id"] == inbound.id
    assert err["reason"] == "streaming_exception"
    assert proxy._active_streams == set()


@pytest.mark.asyncio
async def test_send_streaming_stream_error_publish_failure_clears_active_streams() -> (
    None
):  # noqa: E501
    """When stream_error publish itself fails, _active_streams is still cleared.

    Adapters depending on stream_end/stream_error WILL hang in this scenario —
    this test makes the missing alarm explicit and pins the finally-block invariant.
    """
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-double-fail")

    # Capture _active_streams between call-1 (success) and call-2 (failure) so
    # the test proves the add→discard lifecycle ran (not just that the set is
    # empty at the end — which would also be true if add() never fired).
    mid_flight_snapshot: set[str] = set()
    call_count = 0

    async def _publish_with_double_failure(_subject, _payload):
        nonlocal call_count
        call_count += 1
        # call 1: chunk seq=0 succeeds; call 2: chunk seq=1 raises (outer except);
        # call 3: stream_error publish itself raises nats.errors.Error (inner except).
        if call_count == 1:
            mid_flight_snapshot.update(proxy._active_streams)
        if call_count == 2:
            raise Exception("NATS down")
        if call_count == 3:
            raise nats.errors.Error("NATS still down")

    nc.publish = AsyncMock(side_effect=_publish_with_double_failure)

    await proxy.send_streaming(
        inbound,
        _async_iter(
            TextDeltaRenderEvent(message_id="msg-double-fail", delta="a"),
            TextDeltaRenderEvent(message_id="msg-double-fail", delta="b"),
        ),
    )

    # Lifecycle invariant: stream_id was tracked mid-flight, then cleared by finally.
    assert "msg-double-fail" in mid_flight_snapshot
    assert proxy._active_streams == set()
    # Confirm inner stream_error publish ran (chunk-1 + chunk-2 + stream_error = 3).
    assert nc.publish.await_count == 3


@pytest.mark.asyncio
async def test_shutdown_loop_calls_publish_stream_errors_on_each_proxy() -> None:
    """Shutdown loop pattern calls publish_stream_errors on each proxy."""
    nc1 = _make_nc()
    nc2 = _make_nc()
    proxy1 = NatsChannelProxy(nc=nc1, platform=Platform.TELEGRAM, bot_id="bot1")
    proxy2 = NatsChannelProxy(nc=nc2, platform=Platform.DISCORD, bot_id="bot2")

    proxy1._active_streams = {"stream-alpha"}
    proxy2._active_streams = {"stream-beta"}

    proxies = [proxy1, proxy2]
    for proxy in proxies:
        await proxy.publish_stream_errors("hub_shutdown")

    # proxy1: published stream_error for stream-alpha
    assert nc1.publish.await_count == 1
    env1 = json.loads(nc1.publish.call_args.args[1].decode("utf-8"))
    assert env1["type"] == "stream_error"
    assert env1["stream_id"] == "stream-alpha"
    assert env1["reason"] == "hub_shutdown"
    assert proxy1._active_streams == set()

    # proxy2: published stream_error for stream-beta
    assert nc2.publish.await_count == 1
    env2 = json.loads(nc2.publish.call_args.args[1].decode("utf-8"))
    assert env2["type"] == "stream_error"
    assert env2["stream_id"] == "stream-beta"
    assert env2["reason"] == "hub_shutdown"
    assert proxy2._active_streams == set()
