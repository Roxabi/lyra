"""Tests for OutboundDispatcher: per-platform outbound queue with CB ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.core.circuit_breaker import CircuitBreaker
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
)
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.trust import TrustLevel


def _make_msg() -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def _make_adapter() -> tuple[MagicMock, OutboundDispatcher]:
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.send_streaming = AsyncMock()
    adapter.render_audio = AsyncMock()
    adapter.render_attachment = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    return adapter, dispatcher


def _make_attachment() -> OutboundAttachment:
    return OutboundAttachment(
        data=b"fake-png",
        type="image",
        mime_type="image/png",
        filename="test.png",
        caption="A test image",
    )


class TestOutboundDispatcherEnqueue:
    async def test_enqueue_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = _make_msg()
            outbound = OutboundMessage.from_text("hi")
            dispatcher.enqueue(msg, outbound)
            # Wait for worker to process
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once_with(msg, outbound)
        finally:
            await dispatcher.stop()

    async def test_enqueue_streaming_delivers_via_adapter(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = _make_msg()

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks())
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_awaited_once()
            call_args = adapter.send_streaming.call_args
            assert call_args[0][0] is msg
        finally:
            await dispatcher.stop()

    async def test_enqueue_streaming_forwards_outbound(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        try:
            msg = _make_msg()
            outbound = OutboundMessage.from_text("")

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks(), outbound)
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_awaited_once()
            call_args = adapter.send_streaming.call_args
            assert call_args[0][0] is msg
            assert call_args[0][2] is outbound
        finally:
            await dispatcher.stop()

    async def test_qsize_reflects_pending_items(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=lambda *_: asyncio.sleep(1))
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            msg = _make_msg()
            # Enqueue 3 items quickly — worker is blocked on the first
            dispatcher.enqueue(msg, OutboundMessage.from_text("1"))
            dispatcher.enqueue(msg, OutboundMessage.from_text("2"))
            dispatcher.enqueue(msg, OutboundMessage.from_text("3"))
            assert dispatcher.qsize() >= 2  # 3 enqueued, worker blocked on 1st
        finally:
            await dispatcher.stop()


class TestOutboundDispatcherCircuitBreaker:
    async def test_open_circuit_drops_message(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()  # trip the circuit
        assert cb.is_open()

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = _make_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            # Circuit is open — adapter.send should NOT be called
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_open_circuit_drops_streaming_and_sets_sentinel(self) -> None:
        adapter = MagicMock()
        adapter.send_streaming = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = _make_msg()
            outbound = OutboundMessage.from_text("")

            async def chunks() -> AsyncIterator[str]:
                yield "hello"

            dispatcher.enqueue_streaming(msg, chunks(), outbound)
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_not_awaited()
            assert outbound.metadata["reply_message_id"] is None
        finally:
            await dispatcher.stop()

    async def test_successful_send_records_cb_success(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()

        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        # Put in half-open state: open then let recovery time elapse (mock)
        from lyra.core.circuit_breaker import CircuitState

        cb._state = CircuitState.HALF_OPEN

        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = _make_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            adapter.send.assert_awaited_once()
            # CB should be closed after successful send
            from lyra.core.circuit_breaker import CircuitState

            assert cb._state == CircuitState.CLOSED
        finally:
            await dispatcher.stop()

    async def test_failed_send_records_cb_failure(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=Exception("network error"))

        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = _make_msg()
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            assert cb._failure_count >= 1
        finally:
            await dispatcher.stop()

    async def test_stop_cancels_worker(self) -> None:
        adapter, dispatcher = _make_adapter()
        await dispatcher.start()
        assert dispatcher._worker is not None
        await dispatcher.stop()
        assert dispatcher._worker is None


# ---------------------------------------------------------------------------
# RED — #138: OutboundDispatcher accepts OutboundMessage (Slice V2)
# ---------------------------------------------------------------------------


def test_enqueue_accepts_outbound_message() -> None:
    """OutboundDispatcher.enqueue() must accept an OutboundMessage payload
    without raising TypeError (issue #138, U3)."""
    from lyra.core.message import OutboundMessage

    # Arrange
    adapter = MagicMock()
    adapter.send = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    mock_msg = _make_msg()
    outbound = OutboundMessage.from_text("test")

    # Act / Assert — must not raise TypeError
    dispatcher.enqueue(mock_msg, outbound)


# ---------------------------------------------------------------------------
# #175: OutboundDispatcher.enqueue_audio() — CB ownership for render_audio()
# ---------------------------------------------------------------------------


class TestOutboundDispatcherAudio:
    async def test_enqueue_audio_delivers_via_adapter(self) -> None:
        from lyra.core.circuit_breaker import CircuitState

        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        cb._state = CircuitState.HALF_OPEN
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            audio = OutboundAudio(audio_bytes=b"fake-ogg", mime_type="audio/ogg")
            dispatcher.enqueue_audio(inbound, audio)
            await asyncio.sleep(0.05)
            adapter.render_audio.assert_awaited_once_with(audio, inbound)
            assert cb._state == CircuitState.CLOSED
        finally:
            await dispatcher.stop()

    async def test_open_circuit_drops_audio(self) -> None:
        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            audio = OutboundAudio(audio_bytes=b"fake-ogg", mime_type="audio/ogg")
            dispatcher.enqueue_audio(inbound, audio)
            await asyncio.sleep(0.05)
            adapter.render_audio.assert_not_awaited()
            assert dispatcher.qsize() == 0
        finally:
            await dispatcher.stop()

    async def test_failed_audio_records_cb_failure(self) -> None:
        adapter = MagicMock()
        adapter.render_audio = AsyncMock(side_effect=Exception("tts error"))
        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            audio = OutboundAudio(audio_bytes=b"fake-ogg", mime_type="audio/ogg")
            dispatcher.enqueue_audio(inbound, audio)
            await asyncio.sleep(0.05)
            assert cb._failure_count >= 1
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# #217: OutboundDispatcher.enqueue_attachment() — CB ownership for render_attachment()
# ---------------------------------------------------------------------------


class TestOutboundDispatcherAttachment:
    async def test_enqueue_attachment_delivers_via_adapter(self) -> None:
        from lyra.core.circuit_breaker import CircuitState

        adapter = MagicMock()
        adapter.render_attachment = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        cb._state = CircuitState.HALF_OPEN
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            attachment = _make_attachment()
            dispatcher.enqueue_attachment(inbound, attachment)
            await asyncio.sleep(0.05)
            adapter.render_attachment.assert_awaited_once_with(attachment, inbound)
            assert cb._state == CircuitState.CLOSED
        finally:
            await dispatcher.stop()

    async def test_open_circuit_drops_attachment(self) -> None:
        adapter = MagicMock()
        adapter.render_attachment = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            attachment = _make_attachment()
            dispatcher.enqueue_attachment(inbound, attachment)
            await asyncio.sleep(0.05)
            adapter.render_attachment.assert_not_awaited()
            assert dispatcher.qsize() == 0
        finally:
            await dispatcher.stop()

    async def test_failed_attachment_records_cb_failure(self) -> None:
        adapter = MagicMock()
        adapter.render_attachment = AsyncMock(side_effect=Exception("send error"))
        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            attachment = _make_attachment()
            dispatcher.enqueue_attachment(inbound, attachment)
            await asyncio.sleep(0.05)
            assert cb._failure_count >= 1
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# #182: OutboundDispatcher.enqueue_audio_stream() — CB ownership for streaming audio
# ---------------------------------------------------------------------------


class TestOutboundDispatcherAudioStream:
    async def test_enqueue_audio_stream_delivers_via_adapter(self) -> None:
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            inbound = _make_msg()

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                yield OutboundAudioChunk(
                    chunk_bytes=b"data",
                    session_id="s1",
                    chunk_index=0,
                    is_final=True,
                )

            it = chunks()
            dispatcher.enqueue_audio_stream(inbound, it)
            await asyncio.sleep(0.05)
            adapter.render_audio_stream.assert_awaited_once()
            call_args = adapter.render_audio_stream.call_args[0]
            assert call_args[0] is it
            assert call_args[1] is inbound

        finally:
            await dispatcher.stop()

    async def test_open_circuit_drops_audio_stream_and_drains(self) -> None:
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            drained: list[int] = []

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                for i in range(3):
                    drained.append(i)
                    yield OutboundAudioChunk(
                        chunk_bytes=b"x",
                        session_id="s1",
                        chunk_index=i,
                        is_final=(i == 2),
                    )

            dispatcher.enqueue_audio_stream(inbound, chunks())
            await asyncio.sleep(0.05)
            adapter.render_audio_stream.assert_not_awaited()
            assert len(drained) == 3  # iterator fully consumed

        finally:
            await dispatcher.stop()

    async def test_failed_audio_stream_records_cb_failure(self) -> None:
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock(side_effect=Exception("stream error"))
        cb = CircuitBreaker(name="telegram", failure_threshold=5)
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                yield OutboundAudioChunk(
                    chunk_bytes=b"data",
                    session_id="s1",
                    chunk_index=0,
                    is_final=True,
                )

            dispatcher.enqueue_audio_stream(inbound, chunks())
            await asyncio.sleep(0.05)
            assert cb._failure_count >= 1

        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# #256: OutboundDispatcher.enqueue_voice_stream() — TTS→VoiceClient pipe
# ---------------------------------------------------------------------------


class TestOutboundDispatcherVoiceStream:
    async def test_enqueue_voice_stream_delivers_via_adapter(self) -> None:
        # Arrange
        adapter = MagicMock()
        adapter.render_voice_stream = AsyncMock()
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            inbound = _make_msg()

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                yield OutboundAudioChunk(
                    chunk_bytes=b"pcm",
                    session_id="s1",
                    chunk_index=0,
                    is_final=True,
                )

            it = chunks()
            dispatcher.enqueue_voice_stream(inbound, it)
            await asyncio.sleep(0.05)

            # Assert — render_voice_stream(chunks, inbound) — chunks first
            adapter.render_voice_stream.assert_awaited_once()
            call_args = adapter.render_voice_stream.call_args[0]
            assert call_args[0] is it
            assert call_args[1] is inbound

        finally:
            await dispatcher.stop()

    async def test_voice_stream_circuit_open_drains_iterator(self) -> None:
        # Arrange — circuit open → iterator must be drained, render NOT called
        adapter = MagicMock()
        adapter.render_voice_stream = AsyncMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            inbound = _make_msg()
            drained: list[int] = []

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                for i in range(3):
                    drained.append(i)
                    yield OutboundAudioChunk(
                        chunk_bytes=b"x",
                        session_id="s1",
                        chunk_index=i,
                        is_final=(i == 2),
                    )

            dispatcher.enqueue_voice_stream(inbound, chunks())
            await asyncio.sleep(0.05)

            # Assert
            adapter.render_voice_stream.assert_not_awaited()
            assert len(drained) == 3  # iterator fully consumed (no generator leak)

        finally:
            await dispatcher.stop()

    async def test_voice_stream_routing_mismatch_drains_iterator(self) -> None:
        # Arrange — dispatcher is "discord", message routing is "telegram"
        adapter = MagicMock()
        adapter.render_voice_stream = AsyncMock()

        dispatcher = OutboundDispatcher(
            platform_name="discord", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            # Build a message whose routing points to telegram — mismatch
            inbound = _make_msg()  # platform="telegram"
            drained: list[int] = []

            async def chunks() -> AsyncIterator[OutboundAudioChunk]:
                for i in range(2):
                    drained.append(i)
                    yield OutboundAudioChunk(
                        chunk_bytes=b"x",
                        session_id="s1",
                        chunk_index=i,
                        is_final=(i == 1),
                    )

            dispatcher.enqueue_voice_stream(inbound, chunks())
            await asyncio.sleep(0.05)

            # Assert — iterator drained, render not called
            adapter.render_voice_stream.assert_not_awaited()
            assert len(drained) == 2

        finally:
            await dispatcher.stop()
