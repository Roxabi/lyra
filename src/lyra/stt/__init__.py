"""STT service — thin wrapper around voiceCLI (faster-whisper + personal vocab)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

log = logging.getLogger(__name__)


@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, path: Path | str) -> "TranscriptionResult": ...


class STTUnavailableError(Exception):
    """Raised when the STT NATS adapter is unreachable (timeout or connection error)."""


class STTNoiseError(Exception):
    """Raised when the transcription result is empty, too short, or a noise token.

    The STT adapter is the owner of noise detection — middleware and agents catch
    this to dispatch the stt_noise template without re-implementing the logic.
    """


WHISPER_NOISE_TOKENS = {"[music]", "[applause]", "[laughter]", "[silence]", "[noise]"}
_ALLOWED_AUDIO_EXTENSIONS = {".ogg", ".mp3", ".wav", ".m4a", ".webm", ".flac", ".opus"}


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float


class STTConfig(BaseModel):
    model_size: str
    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


def load_stt_config() -> STTConfig:
    model_size = os.environ.get("LYRA_STT_MODEL") or "large-v3-turbo"
    return STTConfig(model_size=model_size)


def is_whisper_noise(text: str) -> bool:
    """Return True if the text is empty or a known Whisper noise token."""
    stripped = text.strip().lower()
    return not stripped or stripped in WHISPER_NOISE_TOKENS


class STTService:
    """Async STT service delegating to voiceCLI (faster-whisper + personal vocab)."""

    def __init__(self, config: STTConfig) -> None:
        self._model = config.model_size
        self._detection_threshold = config.language_detection_threshold
        self._detection_segments = config.language_detection_segments
        self._detection_fallback = config.language_fallback
        self._daemon_active = False
        log.debug("STTService init: model=%s (via voiceCLI)", self._model)

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        resolved = Path(path).resolve()
        _tmpdir = Path(tempfile.gettempdir()).resolve()
        if not resolved.is_relative_to(_tmpdir):
            raise ValueError(
                f"Path outside allowed directory ({_tmpdir}): {resolved}"
            )
        if resolved.suffix.lower() not in _ALLOWED_AUDIO_EXTENSIONS:
            raise ValueError(
                f"Unsupported audio extension {resolved.suffix!r},"
                f" expected one of {sorted(_ALLOWED_AUDIO_EXTENSIONS)}"
            )
        if not resolved.is_file():
            raise FileNotFoundError(f"Audio file not found: {resolved}")
        return await asyncio.to_thread(self._transcribe_sync, str(resolved))

    def _transcribe_sync(self, path: str) -> TranscriptionResult:
        try:
            from voicecli.config import load_vocab, vocab_to_prompt
            from voicecli.stt_daemon import SOCKET_PATH
            from voicecli.transcribe import transcribe as _transcribe

            self._sync_daemon_state(SOCKET_PATH)
            initial_prompt = vocab_to_prompt(load_vocab())
            kwargs: dict = dict(model=self._model, initial_prompt=initial_prompt)
            if self._detection_threshold is not None:
                kwargs["language_detection_threshold"] = self._detection_threshold
            if self._detection_segments is not None:
                kwargs["language_detection_segments"] = self._detection_segments
            if self._detection_fallback is not None:
                kwargs["language_fallback"] = self._detection_fallback
            try:
                vc_result = _transcribe(Path(path), **kwargs)
            except (ConnectionError, OSError):
                if self._daemon_active:
                    self._daemon_active = False
                    log.warning(
                        "STT daemon connection failed — retrying with in-process model",
                    )
                    vc_result = _transcribe(Path(path), **kwargs)
                else:
                    raise

            duration = (
                max((seg["end"] for seg in vc_result.segments), default=0.0)
                if vc_result.segments
                else 0.0
            )
            result = TranscriptionResult(
                text=vc_result.text,
                language=vc_result.language or "unknown",
                duration_seconds=duration,
            )
            if is_whisper_noise(result.text):
                log.info(
                    "STT noise result: path=%s lang=%s text=%r",
                    path, result.language, result.text,
                )
                raise STTNoiseError(f"Noise transcript: {result.text!r}")
            log.info(
                "Transcription complete: path=%s lang=%s dur=%.2fs text_len=%d",
                path, result.language, result.duration_seconds, len(result.text),
            )
            return result
        except STTNoiseError:
            raise
        except Exception:
            log.exception("Transcription failed: path=%s model=%s", path, self._model)
            raise

    def _sync_daemon_state(self, socket_path: Path) -> None:  # noqa: ANN001
        """Update _daemon_active flag and unload/reload local model as needed."""
        daemon_up = socket_path.exists()
        if daemon_up and not self._daemon_active:
            from voicecli.transcribe import unload_model  # type: ignore[import-untyped]  # noqa: I001

            unload_model()
            self._daemon_active = True
            log.info("STT daemon detected — unloaded local model, deferring to daemon")
        elif not daemon_up and self._daemon_active:
            self._daemon_active = False
            log.warning("STT daemon socket gone — falling back to in-process model")
