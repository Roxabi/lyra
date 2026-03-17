"""Tests for TTSService and TTSConfig (voiceCLI backend)."""

from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent_builder import _build_tts_from_dict
from lyra.core.agent_config import AgentTTSConfig
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
    """synthesize() returns SynthesisResult with OGG audio_bytes and audio/ogg mime."""
    svc = TTSService(TTSConfig())

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    _write_minimal_wav(wav_path, duration_ms=500)
    expected_bytes = b"fakeoggdata"
    Path(ogg_path).write_bytes(expected_bytes)

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=500)
        return _make_chunked_result([wav_path])

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

    _write_minimal_wav(wav_path, duration_ms=200)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_generate(text, **kwargs):
        _write_minimal_wav(wav_path, duration_ms=200)
        return _make_chunked_result([wav_path])

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

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

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

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello")  # no language override

    assert captured.get("language") == "English"


# ---------------------------------------------------------------------------
# _wav_duration_ms() — utility
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AgentTTSConfig — new fields (exaggeration, cfg_weight)
# ---------------------------------------------------------------------------


def test_agent_tts_config_has_exaggeration_and_cfg_weight():
    """AgentTTSConfig has exaggeration and cfg_weight float fields."""
    cfg = AgentTTSConfig(exaggeration=0.6, cfg_weight=0.5)
    assert cfg.exaggeration == 0.6
    assert cfg.cfg_weight == 0.5


def test_agent_tts_config_defaults_none():
    """New fields default to None."""
    cfg = AgentTTSConfig()
    assert cfg.exaggeration is None
    assert cfg.cfg_weight is None


def test_build_tts_from_dict_extracts_new_fields():
    """_build_tts_from_dict() extracts exaggeration and cfg_weight."""
    data = {"engine": "qwen", "exaggeration": 0.7, "cfg_weight": 0.3}
    cfg = _build_tts_from_dict(data)
    assert cfg.exaggeration == 0.7
    assert cfg.cfg_weight == 0.3
    assert cfg.engine == "qwen"


def test_build_tts_from_dict_missing_new_fields():
    """_build_tts_from_dict() returns None for missing new fields."""
    cfg = _build_tts_from_dict({"engine": "qwen"})
    assert cfg.exaggeration is None
    assert cfg.cfg_weight is None


# ---------------------------------------------------------------------------
# synthesize() with agent_tts — per-agent config override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_with_agent_tts_forwards_all_fields():
    """agent_tts forwards all non-None fields to generate_async."""
    svc = TTSService(TTSConfig(engine="global_engine", voice="global_voice"))
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    agent_tts = AgentTTSConfig(
        engine="agent_engine",
        voice="agent_voice",
        language="French",
        accent="Parisien",
        personality="Chaleureuse",
        speed="Rapide",
        emotion="Enthousiaste",
        exaggeration=0.6,
        cfg_weight=0.5,
        segment_gap=200,
        crossfade=50,
        chunk_size=300,
    )

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello", agent_tts=agent_tts)

    assert captured.get("engine") == "agent_engine"
    assert captured.get("voice") == "agent_voice"
    assert captured.get("language") == "French"  # full name, not ISO code
    assert captured.get("accent") == "Parisien"
    assert captured.get("personality") == "Chaleureuse"
    assert captured.get("speed") == "Rapide"
    assert captured.get("emotion") == "Enthousiaste"
    assert captured.get("exaggeration") == 0.6
    assert captured.get("cfg_weight") == 0.5
    assert captured.get("segment_gap") == 200
    assert captured.get("crossfade") == 50
    assert captured.get("chunk_size") == 300


@pytest.mark.asyncio
async def test_synthesize_agent_tts_merge_order():
    """User pref overrides agent_tts for language/voice; agent_tts overrides global."""
    svc = TTSService(TTSConfig(engine="global", voice="global_v", language="English"))
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    agent_tts = AgentTTSConfig(
        engine="agent_engine", voice="agent_voice", language="French"
    )

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            # User pref overrides agent_tts for language and voice
            await svc.synthesize(
                "Hello", agent_tts=agent_tts, language="de", voice="user_voice"
            )

    # user pref wins for language and voice
    assert captured.get("language") == "german"  # "de" normalized
    assert captured.get("voice") == "user_voice"
    # agent_tts wins for engine (no user-pref layer)
    assert captured.get("engine") == "agent_engine"


@pytest.mark.asyncio
async def test_synthesize_agent_tts_chunked_not_forwarded():
    """chunked is always True (hardcoded), never overridden by agent_tts."""
    svc = TTSService(TTSConfig())
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    agent_tts = AgentTTSConfig(chunked=False)

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello", agent_tts=agent_tts)

    assert captured.get("chunked") is True


@pytest.mark.asyncio
async def test_synthesize_agent_tts_none_uses_global_defaults():
    """synthesize(agent_tts=None) uses global defaults — no regression."""
    svc = TTSService(TTSConfig(engine="global_eng", voice="global_vox"))
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    _write_minimal_wav(wav_path)
    Path(ogg_path).write_bytes(b"fakeogg")

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        _write_minimal_wav(wav_path)
        return _make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        with patch("lyra.tts._wav_to_ogg", return_value=Path(ogg_path)):
            await svc.synthesize("Hello", agent_tts=None)

    assert captured.get("engine") == "global_eng"
    assert captured.get("voice") == "global_vox"


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
