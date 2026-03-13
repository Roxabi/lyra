"""TTS service — thin wrapper around voiceCLI (Qwen TTS + daemon-first queue)."""

from __future__ import annotations

import logging
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None  # None if WAV header unreadable


@dataclass
class TTSConfig:
    engine: str | None = None  # TTS_ENGINE env var
    voice: str | None = None  # TTS_VOICE env var
    language: str | None = None  # TTS_LANGUAGE env var


def load_tts_config() -> TTSConfig:
    return TTSConfig(
        engine=os.environ.get("TTS_ENGINE") or None,
        voice=os.environ.get("TTS_VOICE") or None,
        language=os.environ.get("TTS_LANGUAGE") or None,
    )


def _wav_duration_ms(path: Path) -> int | None:
    """Read WAV header to compute duration in ms. Returns None on error."""
    try:
        with wave.open(str(path)) as wf:
            return int(wf.getnframes() / wf.getframerate() * 1000)
    except Exception:
        return None


class TTSService:
    """Async TTS service delegating to voiceCLI (Qwen TTS, daemon-first / fallback)."""

    def __init__(self, config: TTSConfig) -> None:
        self._engine = config.engine
        self._voice = config.voice
        self._language = config.language
        log.debug(
            "TTSService init: engine=%s voice=%s (via voiceCLI)",
            self._engine,
            self._voice,
        )

    async def synthesize(self, text: str) -> SynthesisResult:
        from voicecli import generate_async

        tmp_fd, tmp_str = tempfile.mkstemp(suffix=".wav")
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)
        result = None
        try:
            result = await generate_async(
                text,
                output=tmp_path,
                engine=self._engine,
                voice=self._voice,
                language=self._language,
            )
            wav_path = result.wav_path
            audio_bytes = wav_path.read_bytes()
            duration_ms = _wav_duration_ms(wav_path)
            log.info(
                "TTS synthesis complete: engine=%s voice=%s text_len=%d duration_ms=%s",
                self._engine or "default",
                self._voice or "default",
                len(text),
                duration_ms,
            )
            return SynthesisResult(
                audio_bytes=audio_bytes,
                mime_type="audio/wav",
                duration_ms=duration_ms,
            )
        except Exception:
            log.exception(
                "TTS synthesis failed: engine=%s text_len=%d", self._engine, len(text)
            )
            raise
        finally:
            tmp_path.unlink(missing_ok=True)
            try:
                if result is not None and result.wav_path != tmp_path:
                    result.wav_path.unlink(missing_ok=True)
            except Exception:
                pass
