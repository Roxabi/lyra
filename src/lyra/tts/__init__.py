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


def _merge_wav_chunks(chunk_paths: list[Path], output: Path) -> Path:
    """Concatenate multiple WAV chunk files into one WAV using stdlib wave.

    All chunks must have identical sample rate, channels, and sample width.
    Returns the merged WAV path (a sibling of *output*).
    """
    wav_chunks = [p for p in chunk_paths if p.suffix == ".wav" and p.exists()]
    if not wav_chunks:
        raise ValueError(f"No valid WAV chunks to merge among {chunk_paths}")
    if len(wav_chunks) == 1:
        return wav_chunks[0]

    merged_path = output.with_suffix(".merged.wav")
    with wave.open(str(wav_chunks[0]), "rb") as first_wf:
        params = first_wf.getparams()

    with wave.open(str(merged_path), "wb") as out_wf:
        out_wf.setparams(params)
        for chunk_path in wav_chunks:
            with wave.open(str(chunk_path), "rb") as wf:
                out_wf.writeframes(wf.readframes(wf.getnframes()))

    log.info("Merged %d WAV chunks → %s", len(wav_chunks), merged_path)
    return merged_path


class TTSService:
    """Async TTS service delegating to voiceCLI (Qwen TTS, daemon-first / fallback).

    Always requests ``chunked=True`` so that long texts are split into safe-sized
    chunks by voiceCLI (avoids Qwen model crashes on large inputs).  All WAV
    chunks are merged via stdlib ``wave`` into a single file, then converted to
    MP3 once.  All intermediate files are cleaned up after the bytes are read.
    """

    def __init__(self, config: TTSConfig) -> None:
        self._engine = config.engine
        self._voice = config.voice
        self._language = config.language
        log.debug(
            "TTSService init: engine=%s voice=%s (via voiceCLI)",
            self._engine,
            self._voice,
        )

    async def synthesize(
        self, text: str, *, language: str | None = None, voice: str | None = None
    ) -> SynthesisResult:
        """Synthesize text to speech.

        language and voice override the instance defaults (self._language, self._voice)
        when non-None. None falls back to the instance value from TTSConfig.
        """
        from voicecli import generate_async

        tmp_fd, tmp_str = tempfile.mkstemp(suffix=".wav")
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)
        result = None
        extra_paths: list[Path] = []
        try:
            result = await generate_async(
                text,
                output=tmp_path,
                engine=self._engine,
                voice=voice if voice is not None else self._voice,
                language=language if language is not None else self._language,
                chunked=True,  # always chunk for long-text reliability
                mp3=False,     # merge chunks manually, then convert once
            )

            # chunked=True always returns chunk_paths; merge WAVs then convert.
            if result.chunk_paths:
                log.info(
                    "TTS merging %d WAV chunks → single MP3",
                    len(result.chunk_paths),
                )
                extra_paths.extend(result.chunk_paths)
                merged_wav = _merge_wav_chunks(result.chunk_paths, tmp_path)
                extra_paths.append(merged_wav)
                from voicecli.utils import wav_to_mp3
                mp3_path = wav_to_mp3(merged_wav)
                extra_paths.append(mp3_path)
            elif result.mp3_path is not None and result.mp3_path.exists():
                mp3_path = result.mp3_path
                extra_paths.append(result.wav_path)  # clean up WAV too
                extra_paths.append(mp3_path)
            else:
                # Daemon didn't produce MP3 — convert manually
                wav_path = result.wav_path
                extra_paths.append(wav_path)
                from voicecli.utils import wav_to_mp3
                mp3_path = wav_to_mp3(wav_path)
                extra_paths.append(mp3_path)

            audio_bytes = mp3_path.read_bytes()
            log.info(
                "TTS synthesis complete: engine=%s voice=%s text_len=%d size=%d bytes",
                self._engine or "default",
                self._voice or "default",
                len(text),
                len(audio_bytes),
            )
            return SynthesisResult(
                audio_bytes=audio_bytes,
                mime_type="audio/mpeg",
                duration_ms=None,  # MP3 duration not read (no lightweight parser)
            )
        except Exception:
            log.exception(
                "TTS synthesis failed: engine=%s text_len=%d", self._engine, len(text)
            )
            raise
        finally:
            tmp_path.unlink(missing_ok=True)
            for p in extra_paths:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
