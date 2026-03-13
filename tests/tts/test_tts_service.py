"""Tests for TTSService and TTSConfig (voiceCLI backend)."""

from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.tts import (
    SynthesisResult,
    TTSConfig,
    TTSService,
    _wav_duration_ms,
    load_tts_config,
)

# ---------------------------------------------------------------------------
# TTSConfig defaults
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = TTSConfig()
    assert cfg.engine is None
    assert cfg.voice is None
    assert cfg.language is None


# ---------------------------------------------------------------------------
# load_tts_config() env vars
# ---------------------------------------------------------------------------


def test_load_tts_config_defaults(monkeypatch):
    monkeypatch.delenv("TTS_ENGINE", raising=False)
    monkeypatch.delenv("TTS_VOICE", raising=False)
    monkeypatch.delenv("TTS_LANGUAGE", raising=False)
    cfg = load_tts_config()
    assert cfg.engine is None
    assert cfg.voice is None
    assert cfg.language is None


def test_load_tts_config_from_env(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    monkeypatch.setenv("TTS_VOICE", "mia")
    monkeypatch.setenv("TTS_LANGUAGE", "English")
    cfg = load_tts_config()
    assert cfg.engine == "qwen"
    assert cfg.voice == "mia"
    assert cfg.language == "English"


# ---------------------------------------------------------------------------
# TTSService.synthesize() — delegates to voiceCLI
# ---------------------------------------------------------------------------


def _write_minimal_wav(path: str, duration_ms: int = 500) -> None:
    """Write a minimal valid WAV file to *path* with given duration."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_ms / 1000)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)


def _make_generate_result(wav_path: str) -> MagicMock:
    """Return a mock voiceCLI generate result with a .wav_path attribute."""
    result = MagicMock()
    result.wav_path = Path(wav_path)
    return result


@pytest.mark.asyncio
async def test_synthesize_returns_synthesis_result():
    """synthesize() returns SynthesisResult with audio_bytes, mime_type, duration_ms."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    _write_minimal_wav(wav_path, duration_ms=500)
    expected_bytes = Path(wav_path).read_bytes()

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=500)
        return _make_generate_result(wav_path)

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        result = await svc.synthesize("Hello world")

    assert isinstance(result, SynthesisResult)
    assert result.audio_bytes == expected_bytes
    assert result.mime_type == "audio/wav"
    assert result.duration_ms is not None
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_success():
    """synthesize() deletes the WAV file returned by voicecli after success."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    _write_minimal_wav(wav_path, duration_ms=200)

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=200)
        return _make_generate_result(wav_path)

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        await svc.synthesize("Cleanup test")

    assert not os.path.exists(wav_path), "WAV file must be deleted after synthesis"


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_failure():
    """synthesize() deletes the temp file even when generate() raises."""
    svc = TTSService(TTSConfig())

    captured_path: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(**kwargs):
        fd, path = real_mkstemp(**kwargs)
        captured_path.append(path)
        return fd, path

    async def fake_generate_error(text, **kwargs):
        raise RuntimeError("TTS backend failed")

    mock_gen = AsyncMock(side_effect=fake_generate_error)
    with patch("tempfile.mkstemp", side_effect=spy_mkstemp):
        with patch("voicecli.generate_async", new=mock_gen):
            with pytest.raises(RuntimeError, match="TTS backend failed"):
                await svc.synthesize("Failure cleanup test")

    assert captured_path, "mkstemp must have been called"
    assert not os.path.exists(captured_path[0]), "temp file must be deleted on failure"


# ---------------------------------------------------------------------------
# _wav_duration_ms() — utility
# ---------------------------------------------------------------------------


def test_wav_duration_ms_none_on_bad_file():
    """_wav_duration_ms returns None for a nonexistent path."""
    assert _wav_duration_ms(Path("/tmp/__nonexistent_lyra_tts_test__.wav")) is None


def test_wav_duration_ms_none_on_corrupt_file():
    """_wav_duration_ms returns None when the file is not a valid WAV."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(b"not a wav file at all \x00\x01\x02")
        tmp_path = tmp.name

    try:
        assert _wav_duration_ms(Path(tmp_path)) is None
    finally:
        os.unlink(tmp_path)


def test_wav_duration_ms_valid_file():
    """_wav_duration_ms returns a positive integer for a valid WAV file."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        _write_minimal_wav(tmp_path, duration_ms=750)
        result = _wav_duration_ms(Path(tmp_path))
        assert result is not None
        assert isinstance(result, int)
        assert result > 0
    finally:
        os.unlink(tmp_path)
