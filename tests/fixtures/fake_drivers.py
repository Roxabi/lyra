"""Fake drivers for integration tests (TTS, STT, LLM)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent
from lyra.llm.base import LlmResult
from lyra.stt import TranscriptionResult
from lyra.tts import SynthesisResult


@dataclass
class FakeTts:
    """Fake TTS driver for testing.

    Records all synthesize calls and returns configurable mock audio.
    Supports error injection via raise_on_synthesize.
    """

    called: bool = False
    last_text: str = ""
    last_voice: str = ""
    last_language: str | None = None
    raise_on_synthesize: Exception | None = None
    _audio_bytes: bytes = field(default_factory=lambda: b"RIFFmock_wav_data")

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts=None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        """Record call and return mock audio bytes."""
        self.called = True
        self.last_text = text
        self.last_voice = voice or ""
        self.last_language = language

        if self.raise_on_synthesize:
            raise self.raise_on_synthesize

        return SynthesisResult(
            audio_bytes=self._audio_bytes,
            mime_type="audio/wav",
            duration_ms=100,
        )


@dataclass
class FakeStt:
    """Fake STT driver for testing.

    Returns preset transcript or raises if configured.
    Records all transcribe calls for assertion.
    """

    preset_transcript: str = "Hello world"
    called: bool = False
    last_audio: bytes = field(default_factory=bytes)
    last_path: Path | str | None = None
    raise_on_transcribe: Exception | None = None

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        """Return preset transcript or raise if configured."""
        self.called = True
        self.last_path = path

        try:
            self.last_audio = Path(path).read_bytes()
        except Exception:
            self.last_audio = b""

        if self.raise_on_transcribe:
            raise self.raise_on_transcribe

        return TranscriptionResult(
            text=self.preset_transcript,
            language="en",
            duration_seconds=2.5,
        )


@dataclass
class FakeClaudeCliDriver:
    """Fake LLM driver for testing.

    Implements the LlmProvider protocol with controllable responses.
    Use queue_response() or queue_error() to preset results for tests.
    """

    capabilities: dict[str, Any] = field(default_factory=dict)
    _queue: list[LlmResult] = field(default_factory=list)
    raise_on_complete: Exception | None = None

    def queue_response(self, text: str, session_id: str = "test-sess") -> None:
        """Queue a successful response to return on next complete() call."""
        self._queue.append(LlmResult(result=text, session_id=session_id))

    def queue_error(self, error: str) -> None:
        """Queue an error response."""
        self._queue.append(LlmResult(error=error))

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        """Return queued LlmResult or raise if raise_on_complete is set."""
        if self.raise_on_complete is not None:
            raise self.raise_on_complete

        if not self._queue:
            return LlmResult(error="No queued response in FakeClaudeCliDriver")

        return self._queue.pop(0)

    async def stream(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Yield queued chunks as LlmEvents."""
        if self.raise_on_complete is not None:
            raise self.raise_on_complete

        if not self._queue:
            yield ResultLlmEvent(
                is_error=True, duration_ms=0, error_text="No queued response"
            )
            return

        result = self._queue.pop(0)
        if result.error:
            yield ResultLlmEvent(
                is_error=True, duration_ms=0, error_text=result.error
            )
            return

        # Yield text as a single chunk, then result event
        yield TextLlmEvent(text=result.result)
        yield ResultLlmEvent(is_error=False, duration_ms=0)

    def is_alive(self, pool_id: str) -> bool:
        """Always return True for tests."""
        return True


__all__ = [
    "FakeClaudeCliDriver",
    "FakeStt",
    "FakeTts",
]
