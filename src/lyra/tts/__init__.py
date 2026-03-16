"""TTS service — thin wrapper around voiceCLI (Qwen TTS + daemon-first queue)."""

from __future__ import annotations

import base64
import logging
import os
import struct
import subprocess
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
    waveform_b64: str | None = None  # 256-byte amplitude array, base64


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


def _wav_waveform_b64(wav_path: Path, num_samples: int = 256) -> str:
    """Compute a 256-byte amplitude waveform from a WAV file (Discord voice message).

    Uses stdlib wave to read raw PCM frames — no subprocess needed.
    Returns base64-encoded bytes of num_samples amplitude values (0-255).
    Falls back to a flat (silent) waveform on any error.
    """
    try:
        with wave.open(str(wav_path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sampwidth == 1:
            # 8-bit unsigned PCM
            samples = [raw[i] - 128 for i in range(0, len(raw), n_channels)]
            max_val = 128
        elif sampwidth == 2:
            # 16-bit signed PCM (most common)
            samples = [
                struct.unpack_from("<h", raw, i)[0]
                for i in range(0, len(raw) - 1, 2 * n_channels)
            ]
            max_val = 32768
        else:
            return base64.b64encode(bytes(num_samples)).decode()

        chunk = max(1, len(samples) // num_samples)
        waveform = bytearray()
        for i in range(num_samples):
            sl = samples[i * chunk : i * chunk + chunk]
            amp = sum(abs(x) for x in sl) // len(sl) if sl else 0
            waveform.append(min(255, int(amp * 255 / max_val)))

        return base64.b64encode(bytes(waveform)).decode()
    except Exception:
        log.warning("waveform computation failed — using flat waveform", exc_info=True)
        return base64.b64encode(bytes(num_samples)).decode()


def _wav_to_ogg(wav_path: Path) -> Path:
    """Convert WAV to OGG/Opus (48 kHz, mono) using ffmpeg. Returns the OGG path."""
    ogg_path = wav_path.with_suffix(".ogg")
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(wav_path),
            "-c:a",
            "libopus",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-y",
            str(ogg_path),
        ],
        capture_output=True,
        check=True,
    )
    return ogg_path


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


# qwen_tts expects full language names, not ISO 639-1 codes
_LANG_ISO_TO_QWEN: dict[str, str] = {
    "zh": "chinese",
    "en": "english",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "ja": "japanese",
    "ko": "korean",
    "pt": "portuguese",
    "ru": "russian",
    "es": "spanish",
}


def _normalize_language(lang: str | None) -> str | None:
    if lang is None:
        return None
    return _LANG_ISO_TO_QWEN.get(lang.lower(), lang)


class TTSService:
    """Async TTS service delegating to voiceCLI (Qwen TTS, daemon-first / fallback).

    Always requests ``chunked=True`` so that long texts are split into safe-sized
    chunks by voiceCLI (avoids Qwen model crashes on large inputs).  All WAV
    chunks are merged via stdlib ``wave`` into a single file, then converted to
    OGG/Opus once.  Duration and waveform are computed from the merged WAV
    before conversion.  All intermediate files are cleaned up after the bytes
    are read.
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

        Returns OGG/Opus audio with duration_ms and waveform_b64 populated.
        """
        from voicecli import generate_async

        tmp_fd, tmp_str = tempfile.mkstemp(suffix=".wav")
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)
        extra_paths: list[Path] = []
        try:
            effective_lang = language if language is not None else self._language
            resolved_lang = _normalize_language(effective_lang)
            result = await generate_async(
                text,
                output=tmp_path,
                engine=self._engine,
                voice=voice if voice is not None else self._voice,
                language=resolved_lang,
                chunked=True,  # always chunk — avoids Qwen crashes on large inputs
                mp3=False,  # keep WAV; merge chunks then convert to OGG
            )

            # Resolve merged WAV path from chunk results
            if result.chunk_paths:
                log.info(
                    "TTS merging %d WAV chunks → single WAV",
                    len(result.chunk_paths),
                )
                extra_paths.extend(result.chunk_paths)
                merged_wav = _merge_wav_chunks(result.chunk_paths, tmp_path)
                extra_paths.append(merged_wav)
            else:
                merged_wav = result.wav_path
                extra_paths.append(merged_wav)

            # Compute duration and waveform from WAV (before OGG conversion)
            duration_ms = _wav_duration_ms(merged_wav)
            waveform_b64 = _wav_waveform_b64(merged_wav)

            # Convert merged WAV → OGG/Opus
            ogg_path = _wav_to_ogg(merged_wav)
            extra_paths.append(ogg_path)

            audio_bytes = ogg_path.read_bytes()
            log.info(
                "TTS synthesis complete: engine=%s voice=%s"
                " text_len=%d size=%d bytes duration=%s ms",
                self._engine or "default",
                self._voice or "default",
                len(text),
                len(audio_bytes),
                duration_ms,
            )
            return SynthesisResult(
                audio_bytes=audio_bytes,
                mime_type="audio/ogg",
                duration_ms=duration_ms,
                waveform_b64=waveform_b64,
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
