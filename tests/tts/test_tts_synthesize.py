"""TTSService.synthesize() — core behaviour, cleanup, language/voice."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lyra.tts import (
    SynthesisResult,
    TTSConfig,
    TTSService,
)

from .conftest import make_chunked_result, write_minimal_wav

# ---------------------------------------------------------------------------
# TTSService.synthesize() — delegates to voiceCLI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_returns_synthesis_result():
    """synthesize() returns SynthesisResult with OGG audio_bytes and audio/ogg mime."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    write_minimal_wav(wav_path, duration_ms=500)
    expected_bytes = b"fakeoggdata"
    Path(ogg_path).write_bytes(expected_bytes)

    async def fake_generate(text, **kwargs):
        write_minimal_wav(wav_path, duration_ms=500)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            result = await svc.synthesize("Hello world")

    assert isinstance(result, SynthesisResult)
    assert result.audio_bytes == expected_bytes
    assert result.mime_type == "audio/ogg"
    assert result.duration_ms is not None  # computed from WAV before OGG conversion


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_success():
    """synthesize() deletes temp WAV chunk and OGG files after success."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    write_minimal_wav(wav_path, duration_ms=200)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_generate(text, **kwargs):
        write_minimal_wav(wav_path, duration_ms=200)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_generate)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Cleanup test")

    assert not os.path.exists(wav_path), "WAV file must be deleted after synthesis"
    assert not os.path.exists(ogg_path), "OGG file must be deleted after synthesis"


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
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello", language="fr")

    assert captured.get("language") == "french"  # ISO code normalized to full name


@pytest.mark.asyncio
async def test_synthesize_language_none_uses_init_value():
    """synthesize() with language=None uses the TTSConfig.language from init."""
    svc = TTSService(TTSConfig(language="English"))
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello")  # no language override

    assert captured.get("language") == "English"
