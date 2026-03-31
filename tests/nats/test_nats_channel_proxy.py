"""Tests for NatsChannelProxy — ChannelAdapter over NATS.

Uses unittest.mock.AsyncMock for the NATS client so no real NATS server is
needed. Each test verifies subject routing and envelope structure independently.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import TextRenderEvent, ToolSummaryRenderEvent
from lyra.core.trust import TrustLevel
from lyra.nats.nats_channel_proxy import NatsChannelProxy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc() -> AsyncMock:
    """Return a mock NATS client with an async publish method."""
    nc = MagicMock()
    nc.publish = AsyncMock()
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
# normalize / normalize_audio — must raise NotImplementedError
# ---------------------------------------------------------------------------


def test_normalize_raises() -> None:
    """normalize() raises NotImplementedError — proxy does not handle inbound."""
    proxy = NatsChannelProxy(nc=_make_nc(), platform=Platform.TELEGRAM, bot_id="main")
    with pytest.raises(
        NotImplementedError, match="does not normalize inbound messages"
    ):
        proxy.normalize({})


def test_normalize_audio_raises() -> None:
    """normalize_audio() raises NotImplementedError."""
    proxy = NatsChannelProxy(nc=_make_nc(), platform=Platform.TELEGRAM, bot_id="main")
    with pytest.raises(NotImplementedError, match="does not normalize inbound audio"):
        proxy.normalize_audio({}, b"", "audio/ogg", trust_level=TrustLevel.PUBLIC)


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
    """send_streaming() assigns seq numbers starting from 0."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-stream")

    tool_event = ToolSummaryRenderEvent(bash_commands=["make test"], is_complete=False)
    text_event = TextRenderEvent(text="Done", is_final=True)

    await proxy.send_streaming(inbound, _async_iter(tool_event, text_event))

    assert nc.publish.await_count == 2
    calls = nc.publish.call_args_list

    chunk0 = json.loads(calls[0].args[1].decode("utf-8"))
    chunk1 = json.loads(calls[1].args[1].decode("utf-8"))

    assert chunk0["seq"] == 0
    assert chunk1["seq"] == 1


@pytest.mark.asyncio
async def test_send_streaming_subject_is_single_outbound_subject() -> None:
    """send_streaming() publishes to single outbound subject, not stream.* subject."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-42")

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="Hi", is_final=True))
    )

    subject, _ = nc.publish.call_args.args
    assert subject == "lyra.outbound.telegram.main"
    assert "stream" not in subject


@pytest.mark.asyncio
async def test_send_streaming_done_true_on_final_text_event() -> None:
    """send_streaming() sets done=True when TextRenderEvent.is_final=True."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="Done", is_final=True))
    )

    _, payload = nc.publish.call_args.args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["done"] is True


@pytest.mark.asyncio
async def test_send_streaming_done_false_on_non_final_event() -> None:
    """send_streaming() sets done=False when is_final/is_complete are False."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="Partial", is_final=False))
    )

    _, payload = nc.publish.call_args.args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["done"] is False


@pytest.mark.asyncio
async def test_send_streaming_event_type_text() -> None:
    """send_streaming() sets event_type='text' for TextRenderEvent."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="Hello", is_final=True))
    )

    _, payload = nc.publish.call_args.args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["event_type"] == "text"


@pytest.mark.asyncio
async def test_send_streaming_event_type_tool_summary() -> None:
    """send_streaming() sets event_type='tool_summary' for ToolSummaryRenderEvent."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    await proxy.send_streaming(
        inbound,
        _async_iter(ToolSummaryRenderEvent(bash_commands=["ls"], is_complete=True)),
    )

    _, payload = nc.publish.call_args.args
    chunk = json.loads(payload.decode("utf-8"))
    assert chunk["event_type"] == "tool_summary"
    assert chunk["done"] is True


@pytest.mark.asyncio
async def test_send_streaming_chunk_has_stream_id_no_type() -> None:
    """Each chunk envelope has stream_id (not msg_id) and no 'type' key."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-check")

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="x", is_final=True))
    )

    _, payload = nc.publish.call_args.args
    chunk = json.loads(payload.decode("utf-8"))
    assert "type" not in chunk
    assert chunk["stream_id"] == "msg-check"
    assert "msg_id" not in chunk
    assert "payload" in chunk


@pytest.mark.asyncio
async def test_send_streaming_drains_iterator_on_publish_failure() -> None:
    """On NATS publish failure, remaining events are drained (no hang)."""
    nc = _make_nc()
    nc.publish = AsyncMock(side_effect=Exception("NATS connection lost"))
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound()

    drained = []

    async def _events():
        yield TextRenderEvent(text="first", is_final=False)
        yield TextRenderEvent(text="second", is_final=False)
        drained.append("second")
        yield TextRenderEvent(text="third", is_final=True)
        drained.append("third")

    # Should not raise; should drain remaining events
    await proxy.send_streaming(inbound, _events())

    # The second and third events must have been drained without publishing
    assert "second" in drained
    assert "third" in drained


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
async def test_render_audio_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """render_audio() logs a warning and does not publish to NATS."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-audio")
    audio = OutboundAudio(audio_bytes=b"\x00\x01", mime_type="audio/ogg")

    with caplog.at_level(logging.WARNING, logger="lyra.nats.nats_channel_proxy"):
        await proxy.render_audio(audio, inbound)

    nc.publish.assert_not_awaited()
    assert any("audio-over-NATS" in r.message for r in caplog.records)
    assert any("msg-audio" in r.message for r in caplog.records)


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
    assert any("audio-over-NATS" in r.message for r in caplog.records)


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
    assert any("audio-over-NATS" in r.message for r in caplog.records)


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


@pytest.mark.asyncio
async def test_send_streaming_uses_single_subject() -> None:
    """send_streaming() publishes to single outbound subject, not stream.* subject."""
    nc = _make_nc()
    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("msg-single-subject")

    await proxy.send_streaming(
        inbound, _async_iter(TextRenderEvent(text="hi", is_final=True))
    )

    assert nc.publish.await_count >= 1
    subject = nc.publish.call_args_list[0].args[0]
    assert "stream" not in subject
    assert subject == f"lyra.outbound.{proxy._platform.value}.{proxy._bot_id}"
    envelope = json.loads(nc.publish.call_args_list[0].args[1])
    assert "stream_id" in envelope
    assert "seq" in envelope
    assert "type" not in envelope  # chunks don't have type key
