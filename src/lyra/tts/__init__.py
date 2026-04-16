"""TTS service — thin wrapper around voiceCLI (Qwen TTS + daemon-first queue)."""

from __future__ import annotations

import base64
import logging
import os
import re
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from lyra.core.agent_config import AgentTTSConfig
    from lyra.integrations.base import AudioConverter


@runtime_checkable
class TtsProtocol(Protocol):
    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> "SynthesisResult": ...


class TtsUnavailableError(Exception):
    """Raised when the TTS NATS adapter is unreachable (timeout or connection error)."""

log = logging.getLogger(__name__)


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None  # None if WAV header unreadable
    waveform_b64: str | None = None  # 256-byte amplitude array, base64


class TTSConfig(BaseModel):
    engine: str | None = None  # LYRA_TTS_ENGINE env var
    voice: str | None = None  # LYRA_TTS_VOICE env var
    language: str | None = None  # LYRA_TTS_LANGUAGE env var


def load_tts_config() -> TTSConfig:
    return TTSConfig(
        engine=os.environ.get("LYRA_TTS_ENGINE"),
        voice=os.environ.get("LYRA_TTS_VOICE"),
        language=os.environ.get("LYRA_TTS_LANGUAGE"),
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


# Precompiled regex patterns for TTS text normalization
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")  # **bold**
_MD_ITALIC = re.compile(r"\*([^*]+)\*")  # *italic* (single)
_MD_UNDERLINE = re.compile(r"_([^_]+)_")  # _underline_
_MD_CODE = re.compile(r"`([^`]+)`")  # `code`
_MD_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)  # # heading
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url)
_URL = re.compile(r"https?://[^\s<>)\"']+")
_MULTI_SPACE = re.compile(r"\s+")


def _normalize_text_for_tts(text: str) -> str:
    """Normalize text for TTS synthesis.

    Transforms:
    - Markdown syntax (**, *, _, `, #) → stripped
    - Links [text](url) → text
    - URLs → "link" placeholder
    - Multiple spaces → single space
    - Newlines → space

    Returns normalized text ready for voicecli.
    """
    # Strip markdown heading markers
    text = _MD_HEADING.sub("", text)

    # Convert markdown links: [text](url) → text
    text = _MD_LINK.sub(r"\1", text)

    # Strip markdown formatting (order matters: bold before italic)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_UNDERLINE.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)

    # Replace URLs with "link" placeholder
    text = _URL.sub("link", text)

    # Collapse all whitespace (newlines, multiple spaces) to single space
    text = _MULTI_SPACE.sub(" ", text).strip()

    return text


class TTSService:
    """Async TTS service delegating to voiceCLI (Qwen TTS, daemon-first / fallback).

    Always requests ``chunked=True`` so that long texts are split into safe-sized
    chunks by voiceCLI (avoids Qwen model crashes on large inputs).  All WAV
    chunks are merged via stdlib ``wave`` into a single file, then converted to
    OGG/Opus once.  Duration and waveform are computed from the merged WAV
    before conversion.  All intermediate files are cleaned up after the bytes
    are read.
    """

    def __init__(
        self,
        config: TTSConfig,
        converter: "AudioConverter | None" = None,
    ) -> None:
        from lyra.integrations.audio import FfmpegConverter

        self._engine = config.engine
        self._voice = config.voice
        self._language = config.language
        self._converter = converter or FfmpegConverter()
        log.debug(
            "TTSService init: engine=%s voice=%s (via voiceCLI)",
            self._engine,
            self._voice,
        )

    def _build_generate_kwargs(
        self,
        output: Path,
        *,
        agent_tts: "AgentTTSConfig | None",
        language: str | None,
        voice: str | None,
        fallback_language: str | None = None,
    ) -> dict:
        """Merge user pref > agent_tts > fallback_language > global defaults.

        ``chunked`` is always ``True`` (safety hardcode, never overridden).
        """
        a = agent_tts

        # language: user pref > agent_tts > fallback_language (#343) > global
        if language is not None:
            effective_lang = language
        elif a is not None and a.language is not None:
            effective_lang = a.language
        elif fallback_language is not None:
            effective_lang = fallback_language
        else:
            effective_lang = self._language

        # voice: user pref > agent_tts > global
        if voice is not None:
            effective_voice = voice
        elif a is not None and a.voice is not None:
            effective_voice = a.voice
        else:
            effective_voice = self._voice

        # engine: agent_tts > global (no user-pref layer)
        effective_engine = (
            a.engine if a is not None and a.engine is not None else self._engine
        )

        kwargs: dict = {
            "output": output,
            "engine": effective_engine,
            "voice": effective_voice,
            "language": _normalize_language(effective_lang),
            "chunked": True,
            "mp3": False,
        }

        if a is not None:
            for field_name in (
                "accent",
                "personality",
                "speed",
                "emotion",
                "exaggeration",
                "cfg_weight",
                "segment_gap",
                "crossfade",
                "chunk_size",
            ):
                val = getattr(a, field_name, None)
                if val is not None:
                    kwargs[field_name] = val

        return kwargs

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        """Synthesize text to speech.

        Merge order (high → low priority):
        - ``language`` / ``voice`` user-pref overrides (sentinel-based)
        - ``agent_tts`` per-agent config fields
        - ``fallback_language`` agent-level default (#343)
        - ``self._engine / self._voice / self._language`` global defaults

        Returns OGG/Opus audio with duration_ms and waveform_b64 populated.
        """
        from voicecli import generate_async  # type: ignore[import-missing]

        # Normalize text for TTS: strip markdown, collapse whitespace, handle URLs
        text = _normalize_text_for_tts(text)

        tts_tmp = (
            Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))).resolve()
            / "tmp"
        )
        tts_tmp.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_str = tempfile.mkstemp(suffix=".wav", dir=tts_tmp)
        os.close(tmp_fd)
        tmp_path = Path(tmp_str)
        extra_paths: list[Path] = []
        try:
            gen_kwargs = self._build_generate_kwargs(
                tmp_path,
                agent_tts=agent_tts,
                language=language,
                voice=voice,
                fallback_language=fallback_language,
            )
            result = await generate_async(text, **gen_kwargs)

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
            ogg_path = merged_wav.with_suffix(".ogg")
            await self._converter.convert_wav_to_ogg(merged_wav, ogg_path)
            extra_paths.append(ogg_path)

            audio_bytes = ogg_path.read_bytes()
            log.info(
                "TTS synthesis complete: engine=%s voice=%s"
                " text_len=%d size=%d bytes duration=%s ms",
                gen_kwargs.get("engine") or "default",
                gen_kwargs.get("voice") or "default",
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
