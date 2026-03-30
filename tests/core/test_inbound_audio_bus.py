"""Tests for InboundAudioBus: per-platform audio queues."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import cast

import pytest

from lyra.core.inbound_audio_bus import InboundAudioBus
from lyra.core.message import InboundAudio, Platform
from lyra.core.trust import TrustLevel


def _make_audio(platform: Platform = Platform.TELEGRAM) -> InboundAudio:
    return InboundAudio(
        id="audio-1",
        platform=platform.value,
        bot_id="main",
        scope_id="chat:123" if platform == Platform.TELEGRAM else "channel:2",
        user_id="user:1",
        audio_bytes=b"\x00\x01\x02",
        mime_type="audio/ogg",
        duration_ms=1500,
        file_id="file-1",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
    )


class TestInboundAudioBusRegistration:
    def test_register_creates_queue(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=50)
        assert Platform.TELEGRAM in bus._queues
        assert bus._queues[Platform.TELEGRAM].maxsize == 50

    def test_register_after_start_raises(self) -> None:
        bus = InboundAudioBus()
        bus._feeders[Platform.TELEGRAM] = cast("asyncio.Task[None]", asyncio.Future())
        with pytest.raises(RuntimeError, match="after start"):
            bus.register(Platform.DISCORD)

    def test_qsize_zero_initially(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM)
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_qsize_unknown_platform_returns_zero(self) -> None:
        bus = InboundAudioBus()
        assert bus.qsize(Platform.TELEGRAM) == 0

    def test_put_increments_qsize(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        bus.put(Platform.TELEGRAM, _make_audio())
        assert bus.qsize(Platform.TELEGRAM) == 1

    def test_put_raises_queue_full(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        bus.put(Platform.TELEGRAM, _make_audio())
        with pytest.raises(asyncio.QueueFull):
            bus.put(Platform.TELEGRAM, _make_audio())

    def test_put_unregistered_platform_raises(self) -> None:
        bus = InboundAudioBus()
        with pytest.raises(KeyError):
            bus.put(Platform.TELEGRAM, _make_audio())

    def test_registered_platforms(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM)
        bus.register(Platform.DISCORD)
        expected = frozenset({Platform.TELEGRAM, Platform.DISCORD})
        assert bus.registered_platforms() == expected


class TestInboundAudioBusFeeder:
    async def test_feeder_forwards_to_staging(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            audio = _make_audio()
            bus.put(Platform.TELEGRAM, audio)
            received = await asyncio.wait_for(bus.get(), timeout=0.5)
            assert received is audio
        finally:
            await bus.stop()

    async def test_two_platforms_isolated(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=1)
        bus.register(Platform.DISCORD, maxsize=10)
        await bus.start()

        try:
            bus.put(Platform.TELEGRAM, _make_audio(Platform.TELEGRAM))
            with pytest.raises(asyncio.QueueFull):
                bus.put(Platform.TELEGRAM, _make_audio(Platform.TELEGRAM))
            # Discord unaffected
            bus.put(Platform.DISCORD, _make_audio(Platform.DISCORD))
        finally:
            await bus.stop()

    async def test_stop_cancels_feeders(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()
        assert len(bus._feeders) == 1
        await bus.stop()
        assert len(bus._feeders) == 0

    async def test_staging_qsize(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            bus.put(Platform.TELEGRAM, _make_audio())
            # Wait for feeder to forward (avoids sleep-based flake)
            await asyncio.wait_for(bus.get(), timeout=0.5)
            bus.task_done()
            assert bus.staging_qsize() == 0
        finally:
            await bus.stop()

    async def test_task_done(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            bus.put(Platform.TELEGRAM, _make_audio())
            await asyncio.wait_for(bus.get(), timeout=0.5)
            bus.task_done()  # should not raise
        finally:
            await bus.stop()

    async def test_double_start_raises(self) -> None:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=10)
        await bus.start()

        try:
            with pytest.raises(RuntimeError, match="already running"):
                await bus.start()
        finally:
            await bus.stop()
