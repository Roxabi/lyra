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


def _make_chunked_result(chunk_wav_paths: list[str]) -> MagicMock:
    """Return a mock voiceCLI TTSResult in chunked mode (chunk_paths set)."""
    result = MagicMock()
    result.wav_path = Path(chunk_wav_paths[0]).with_suffix(".done")  # sentinel
    result.mp3_path = None
    result.chunk_paths = [Path(p) for p in chunk_wav_paths]
    return result


@pytest.mark.asyncio
async def test_synthesize_returns_synthesis_result():
    """synthesize() returns SynthesisResult with MP3 audio_bytes and audio/mpeg mime."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = tmp_mp3.name

    _write_minimal_wav(wav_path, duration_ms=500)
    expected_bytes = b"fakemp3data"
    Path(mp3_path).write_bytes(expected_bytes)

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=500)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        with patch("voicecli.utils.wav_to_mp3", return_value=Path(mp3_path)):
            result = await svc.synthesize("Hello world")

    assert isinstance(result, SynthesisResult)
    assert result.audio_bytes == expected_bytes
    assert result.mime_type == "audio/mpeg"
    assert result.duration_ms is None  # MP3 duration not read


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_success():
    """synthesize() deletes temp WAV chunk and MP3 files after success."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = tmp_mp3.name

    _write_minimal_wav(wav_path, duration_ms=200)
    Path(mp3_path).write_bytes(b"fakemp3")

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=200)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        with patch("voicecli.utils.wav_to_mp3", return_value=Path(mp3_path)):
            await svc.synthesize("Cleanup test")

    assert not os.path.exists(wav_path), "WAV file must be deleted after synthesis"
    assert not os.path.exists(mp3_path), "MP3 file must be deleted after synthesis"


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
# S1 — T02: synthesize() language/voice params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_passes_language_to_generate():
    """synthesize(language=...) forwards language kwarg to generate_async."""
    svc = TTSService(TTSConfig())
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = tmp_mp3.name

    _write_minimal_wav(wav_path)
    Path(mp3_path).write_bytes(b"fakemp3")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("voicecli.utils.wav_to_mp3", return_value=Path(mp3_path)):
            await svc.synthesize("Hello", language="fr")

    assert captured.get("language") == "fr"


@pytest.mark.asyncio
async def test_synthesize_language_none_uses_init_value():
    """synthesize() with language=None uses the TTSConfig.language from init."""
    svc = TTSService(TTSConfig(language="English"))
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        mp3_path = tmp_mp3.name

    _write_minimal_wav(wav_path)
    Path(mp3_path).write_bytes(b"fakemp3")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("voicecli.utils.wav_to_mp3", return_value=Path(mp3_path)):
            await svc.synthesize("Hello")  # no language override

    assert captured.get("language") == "English"


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
