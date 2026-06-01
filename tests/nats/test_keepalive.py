"""Tests for per-stream keepalive (#687) — T25 + T26.

Covers:
- NatsChannelProxy.send_streaming publishes stream_keepalive envelopes during idle
- decode_stream_events skips keepalive chunks without yielding to caller
- Dead stream (no keepalives) raises StreamChunkTimeout
- Keepalive seq interleaving does not corrupt real-chunk seq ordering
- Old-adapter (pre-PR codec path) does not crash on unknown event_type
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.nats.nats_stream_decoder import decode_stream_events
from lyra.core.auth.trust import TrustLevel
from lyra.core.exceptions import StreamChunkTimeout
from lyra.core.messaging.message import InboundMessage, Platform
from lyra.core.messaging.render_events import TextChunkRenderEvent
from lyra.nats.nats_channel_proxy import NatsChannelProxy
from lyra.nats.render_event_codec import NatsRenderEventCodec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc() -> AsyncMock:
    nc = MagicMock()
    nc.publish = AsyncMock()
    return nc


def _make_inbound(msg_id: str = "msg-ka") -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="user-1",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _ka_chunk(stream_id: str, seq: int) -> dict:
    """Build a keepalive chunk dict as published on the wire."""
    return {
        "stream_id": stream_id,
        "seq": seq,
        "event_type": "stream_keepalive",
        "payload": {},
        "done": False,
    }


def _text_chunk(stream_id: str, seq: int, text: str = "hi") -> dict:
    """Build a text_chunk wire envelope."""
    event = TextChunkRenderEvent(message_id=stream_id, delta=text)
    codec = NatsRenderEventCodec()
    event_type, payload, _ = codec.encode(event)
    return {
        "stream_id": stream_id,
        "seq": seq,
        "event_type": event_type,
        "payload": payload,
        "done": False,
    }


def _stream_end_chunk(stream_id: str, seq: int) -> dict:
    return {
        "stream_id": stream_id,
        "seq": seq,
        "event_type": "stream_end",
        "payload": {},
        "done": True,
    }


async def _drain(stream_id: str, q: asyncio.Queue, **kwargs) -> list:
    results = []
    async for event in decode_stream_events(stream_id, q, **kwargs):
        results.append(event)
    return results


# ---------------------------------------------------------------------------
# T25-1: send_streaming publishes keepalive envelopes during idle
# ---------------------------------------------------------------------------


class TestPublishesKeepaliveDuringIdle:
    """NatsChannelProxy publishes stream_keepalive envelopes when the events
    iterator is idle for longer than KEEPALIVE_INTERVAL_S."""

    @pytest.mark.asyncio
    async def test_publishes_keepalive_during_idle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At least 3 keepalive envelopes published; seq is monotonically increasing."""
        import lyra.nats.keepalive as keepalive_mod  # noqa: PLC0415

        fast_interval = 0.05  # 50 ms
        monkeypatch.setattr(keepalive_mod, "KEEPALIVE_INTERVAL_S", fast_interval)

        nc = _make_nc()
        proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
        inbound = _make_inbound("msg-idle")

        # Events iterator that sleeps for 10 * interval before yielding anything,
        # simulating an LLM tool-call that takes a while.
        async def _slow_events() -> AsyncIterator:
            await asyncio.sleep(10 * fast_interval)
            # Yield nothing — keepalives should have fired before this returns
            return
            yield  # make it an async generator

        await proxy.send_streaming(inbound, _slow_events())

        # Collect all keepalive envelopes from publishes
        keepalives = []
        for call in nc.publish.call_args_list:
            raw = call.args[1]
            data = json.loads(raw.decode("utf-8"))
            if data.get("event_type") == "stream_keepalive":
                keepalives.append(data)

        assert len(keepalives) >= 3, (
            f"Expected >=3 keepalive envelopes, got {len(keepalives)}"
        )

        # Seq fields must be monotonically increasing
        seqs = [ka["seq"] for ka in keepalives]
        for i in range(1, len(seqs)):
            assert seqs[i] > seqs[i - 1], f"seq not monotonically increasing: {seqs}"


# ---------------------------------------------------------------------------
# T25-2: decode_stream_events skips keepalives, yields only real chunks
# ---------------------------------------------------------------------------


class TestDecoderSkipsKeepaliveNoYield:
    """decode_stream_events skips stream_keepalive chunks without yielding."""

    @pytest.mark.asyncio
    async def test_decoder_skips_keepalive_no_yield(self) -> None:
        """Only real-text chunks are yielded; keepalives are transparent."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        stream_id = "msg-skip-ka"

        # Stream: real-chunk, keepalive, keepalive, real-chunk, stream_end
        for chunk in [
            _text_chunk(stream_id, 0, "hello"),
            _ka_chunk(stream_id, 1),
            _ka_chunk(stream_id, 2),
            _text_chunk(stream_id, 3, "world"),
            _stream_end_chunk(stream_id, 4),
        ]:
            await q.put(chunk)

        events = await _drain(stream_id, q)

        assert len(events) == 2, f"Expected 2 real events, got {len(events)}: {events}"
        # No event should be a keepalive sentinel type
        for ev in events:
            assert (
                not hasattr(ev, "event_type")
                or getattr(ev, "event_type", None) != "stream_keepalive"
            )


# ---------------------------------------------------------------------------
# T25-3: dead stream (no keepalives) raises StreamChunkTimeout
# ---------------------------------------------------------------------------


class TestDeadStreamNoKeepaliveRaisesTimeout:
    """An empty queue with no keepalives raises StreamChunkTimeout."""

    @pytest.mark.asyncio
    async def test_dead_stream_no_keepalive_raises_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StreamChunkTimeout raised when no chunks and no keepalives arrive."""
        import lyra.adapters.nats.nats_stream_decoder as mod

        monkeypatch.setattr(mod, "_LIVENESS_POLL_SECONDS", 0.05)
        monkeypatch.setattr(mod, "_CHUNK_TIMEOUT_SECONDS", 0.2)

        q: asyncio.Queue[dict] = asyncio.Queue()

        with pytest.raises(StreamChunkTimeout):
            await _drain("msg-dead", q)


# ---------------------------------------------------------------------------
# T25-4: keepalive seq interleaving does not corrupt real-chunk seq ordering
# ---------------------------------------------------------------------------


class TestKeepaliveSeqDoesNotCorruptRealChunkSeq:
    """Real chunks arrive in order (1, 3, 5) when keepalives occupy seq 2, 4."""

    @pytest.mark.asyncio
    async def test_keepalive_seq_does_not_corrupt_real_chunk_seq(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Real chunks arrive in order; no gap-warnings for keepalive seqs."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        stream_id = "msg-interleave"

        # Interleaved sequence: seq 0 real, seq 1 keepalive, seq 2 real,
        # seq 3 keepalive, seq 4 real, seq 5 stream_end
        chunks = [
            _text_chunk(stream_id, 0, "one"),
            _ka_chunk(stream_id, 1),
            _text_chunk(stream_id, 2, "two"),
            _ka_chunk(stream_id, 3),
            _text_chunk(stream_id, 4, "three"),
            _stream_end_chunk(stream_id, 5),
        ]
        for chunk in chunks:
            await q.put(chunk)

        with caplog.at_level(
            logging.WARNING, logger="lyra.adapters.nats.nats_stream_decoder"
        ):
            events = await _drain(stream_id, q)

        # Exactly 3 real events yielded
        assert len(events) == 3, f"Expected 3 real events, got {len(events)}"

        # No out-of-order warnings — keepalives hold seq positions monotonically
        oo_warnings = [r.message for r in caplog.records if "out-of-order" in r.message]
        assert not oo_warnings, f"Unexpected out-of-order warnings: {oo_warnings}"


# ---------------------------------------------------------------------------
# T26-5: old-adapter does not crash on unknown stream_keepalive event_type
# ---------------------------------------------------------------------------


class TestOldAdapterIgnoresUnknownEventType:
    """Simulates a pre-PR codec path that does not recognize stream_keepalive.

    Patches _SYNTHETIC_NON_TERMINALS to remove stream_keepalive, mirroring a
    pre-T23 codec.  The unknown event_type must NOT crash; it logs a warning
    and returns None.
    """

    def test_old_adapter_ignores_unknown_event_type(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stream_keepalive removed from sentinels: logs warning, returns None."""
        import lyra.nats.render_event_codec as codec_mod

        # Patch _SYNTHETIC_NON_TERMINALS to simulate the pre-PR codec that
        # did not recognize stream_keepalive.
        monkeypatch.setattr(
            codec_mod,
            "_SYNTHETIC_NON_TERMINALS",
            frozenset(),
        )

        codec = NatsRenderEventCodec()
        keepalive_payload: dict = {}

        with caplog.at_level(logging.WARNING, logger="lyra.nats.render_event_codec"):
            result = codec.decode("stream_keepalive", keepalive_payload)

        # Must not raise; must return None (dropped)
        assert result is None

        # A warning must have been logged about the unknown event_type
        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "stream_keepalive" in msg or "unknown" in msg for msg in warning_messages
        ), f"Expected warning about unknown event_type, got: {warning_messages}"


# ---------------------------------------------------------------------------
# Unit: _run_keepalive_loop time guard skips publish when below threshold
# ---------------------------------------------------------------------------


class _FakeTime:
    """Stand-in for the ``time`` module; does not affect global ``time.monotonic``."""

    def __init__(self, value: float) -> None:
        self._value = value

    def monotonic(self) -> float:
        return self._value


class TestKeepaliveTimeGuardSkipsPublish:
    """Negative test for _run_keepalive_loop time guard."""

    @pytest.mark.asyncio
    async def test_keepalive_skipped_when_time_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mock time.monotonic to return just below threshold; nc.publish is skipped."""
        import lyra.nats.keepalive as keepalive_mod  # noqa: PLC0415

        fast_interval = 0.05
        monkeypatch.setattr(keepalive_mod, "KEEPALIVE_INTERVAL_S", fast_interval)

        nc = _make_nc()
        seq_box = [0]
        base_time = 100.0
        last_publish_box = [base_time]

        # Replace the module-local ``time`` reference so global ``time.monotonic``
        # (used by ``asyncio.sleep``) is unaffected.
        fake_time = _FakeTime(base_time + fast_interval - 0.01)
        monkeypatch.setattr(keepalive_mod, "time", fake_time)

        task = asyncio.create_task(
            keepalive_mod._run_keepalive_loop(
                nc, "subject", "stream-id", seq_box, last_publish_box
            )
        )
        await asyncio.sleep(fast_interval * 3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        nc.publish.assert_not_awaited()
