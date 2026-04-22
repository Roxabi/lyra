"""Tests for fake TTS/STT/LLM drivers."""

from pathlib import Path

import pytest
from tests.fixtures.fake_drivers import (
    FakeClaudeCliDriver,
    FakeStt,
    FakeTts,
)

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from lyra.stt import TranscriptionResult
from lyra.tts import SynthesisResult


class TestFakeTts:
    """Tests for FakeTts driver."""

    def test_fake_tts_default_state(self) -> None:
        """Fresh instance should have default state."""
        tts = FakeTts()
        assert tts.called is False
        assert tts.last_text == ""
        assert tts.last_voice == ""
        assert tts.raise_on_synthesize is None

    @pytest.mark.asyncio
    async def test_fake_tts_records_synthesize_call(self) -> None:
        """Should record text and voice from synthesize call."""
        tts = FakeTts()

        result = await tts.synthesize("Test text", voice="alloy")

        assert tts.called is True
        assert tts.last_text == "Test text"
        assert tts.last_voice == "alloy"
        assert isinstance(result, SynthesisResult)
        assert result.audio_bytes == tts._audio_bytes

    @pytest.mark.asyncio
    async def test_fake_tts_returns_synthesis_result(self) -> None:
        """Should return FakeSynthesisResult with expected defaults."""
        tts = FakeTts()

        result = await tts.synthesize("Hello")

        assert result.mime_type == "audio/wav"
        assert result.duration_ms == 100
        assert result.waveform_b64 is None

    @pytest.mark.asyncio
    async def test_fake_tts_records_optional_params(self) -> None:
        """Should record language parameter."""
        tts = FakeTts()

        await tts.synthesize("Test", language="fr", voice="echo")

        assert tts.last_language == "fr"
        assert tts.last_voice == "echo"

    @pytest.mark.asyncio
    async def test_fake_tts_raises_when_configured(self) -> None:
        """Should raise exception when raise_on_synthesize is set."""
        tts = FakeTts(raise_on_synthesize=RuntimeError("TTS unavailable"))

        with pytest.raises(RuntimeError, match="TTS unavailable"):
            await tts.synthesize("Test")

        assert tts.called is True  # Call was recorded before raise


class TestFakeStt:
    """Tests for FakeStt driver."""

    def test_fake_stt_default_state(self) -> None:
        """Fresh instance should have default state."""
        stt = FakeStt()
        assert stt.called is False
        assert stt.preset_transcript == "Hello world"
        assert stt.raise_on_transcribe is None

    @pytest.mark.asyncio
    async def test_fake_stt_returns_preset_transcript(self) -> None:
        """Should return configured preset transcript."""
        stt = FakeStt(preset_transcript="Custom transcript")

        result = await stt.transcribe("/fake/audio.wav")

        assert isinstance(result, TranscriptionResult)
        assert result.text == "Custom transcript"
        assert result.language == "en"

    @pytest.mark.asyncio
    async def test_fake_stt_records_call(self) -> None:
        """Should record the path from transcribe call."""
        stt = FakeStt()

        await stt.transcribe("/path/to/audio.wav")

        assert stt.called is True
        assert stt.last_path == "/path/to/audio.wav"

    @pytest.mark.asyncio
    async def test_fake_stt_raises_when_configured(self) -> None:
        """Should raise exception when raise_on_transcribe is set."""
        stt = FakeStt(raise_on_transcribe=RuntimeError("STT unavailable"))

        with pytest.raises(RuntimeError, match="STT unavailable"):
            await stt.transcribe("/fake/audio.wav")

        assert stt.called is True  # Call was recorded before raise

    @pytest.mark.asyncio
    async def test_fake_stt_accepts_path_object(self) -> None:
        """Should accept Path object as argument."""
        stt = FakeStt(preset_transcript="Path test")

        result = await stt.transcribe(Path("/some/audio.wav"))

        assert result.text == "Path test"
        assert stt.last_path == Path("/some/audio.wav")


class TestFakeClaudeCliDriver:
    """Tests for FakeClaudeCliDriver."""

    @pytest.fixture
    def driver(self) -> FakeClaudeCliDriver:
        """Create a fresh driver instance."""
        return FakeClaudeCliDriver()

    @pytest.fixture
    def model_cfg(self) -> ModelConfig:
        """Create a default model config."""
        return ModelConfig()

    @pytest.mark.asyncio
    async def test_complete_returns_queued_response(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """complete() returns queued response."""
        driver.queue_response("Hello, world!", session_id="sess-123")

        result = await driver.complete(
            pool_id="test-pool",
            text="Hi",
            model_cfg=model_cfg,
            system_prompt="You are helpful.",
        )

        assert result.ok
        assert result.result == "Hello, world!"
        assert result.session_id == "sess-123"

    @pytest.mark.asyncio
    async def test_complete_returns_queued_error(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """complete() returns queued error."""
        driver.queue_error("Something went wrong")

        result = await driver.complete(
            pool_id="test-pool",
            text="Hi",
            model_cfg=model_cfg,
            system_prompt="You are helpful.",
        )

        assert not result.ok
        assert result.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_complete_raises_when_configured(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """complete() raises raise_on_complete exception when set."""
        driver.raise_on_complete = RuntimeError("Connection failed")
        driver.queue_response("Should not be returned")

        with pytest.raises(RuntimeError, match="Connection failed"):
            await driver.complete(
                pool_id="test-pool",
                text="Hi",
                model_cfg=model_cfg,
                system_prompt="You are helpful.",
            )

    @pytest.mark.asyncio
    async def test_complete_returns_error_when_queue_empty(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """complete() returns error result when no response queued."""
        result = await driver.complete(
            pool_id="test-pool",
            text="Hi",
            model_cfg=model_cfg,
            system_prompt="You are helpful.",
        )

        assert not result.ok
        assert "No queued response" in result.error

    @pytest.mark.asyncio
    async def test_stream_yields_text_and_result_events(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """stream() yields TextLlmEvent followed by ResultLlmEvent."""
        driver.queue_response("Hello from stream")

        events = []
        async for event in driver.stream(
            pool_id="test-pool",
            text="Hi",
            model_cfg=model_cfg,
            system_prompt="You are helpful.",
        ):
            events.append(event)

        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert events[0].text == "Hello from stream"
        assert isinstance(events[1], ResultLlmEvent)
        assert not events[1].is_error

    @pytest.mark.asyncio
    async def test_stream_yields_error_event_on_error(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """stream() yields error ResultLlmEvent when queued error."""
        driver.queue_error("Stream error")

        events = []
        async for event in driver.stream(
            pool_id="test-pool",
            text="Hi",
            model_cfg=model_cfg,
            system_prompt="You are helpful.",
        ):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], ResultLlmEvent)
        assert events[0].is_error
        assert events[0].error_text == "Stream error"

    @pytest.mark.asyncio
    async def test_stream_raises_when_configured(
        self, driver: FakeClaudeCliDriver, model_cfg: ModelConfig
    ) -> None:
        """stream() raises raise_on_complete exception when set."""
        driver.raise_on_complete = RuntimeError("Stream connection failed")
        driver.queue_response("Should not be yielded")

        with pytest.raises(RuntimeError, match="Stream connection failed"):
            async for _ in driver.stream(
                pool_id="test-pool",
                text="Hi",
                model_cfg=model_cfg,
                system_prompt="You are helpful.",
            ):
                pass

    def test_is_alive_always_true(self, driver: FakeClaudeCliDriver) -> None:
        """is_alive() always returns True."""
        assert driver.is_alive("any-pool")
        assert driver.is_alive("different-pool")

    def test_queue_fifo_order(self, driver: FakeClaudeCliDriver) -> None:
        """Responses are returned in FIFO order."""
        driver.queue_response("First")
        driver.queue_response("Second")
        driver.queue_response("Third")

        assert driver._queue[0].result == "First"
        assert driver._queue[1].result == "Second"
        assert driver._queue[2].result == "Third"
