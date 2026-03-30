"""Tests for AgentTTSConfig fields and per-agent synthesize() override behaviour."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("voicecli") is None,
    reason="voicecli not installed (optional voice extra)",
)

from lyra.core.agent_builder import _build_tts_from_dict  # noqa: E402
from lyra.core.agent_config import AgentTTSConfig  # noqa: E402
from lyra.tts import TTSConfig, TTSService  # noqa: E402

from .conftest import make_chunked_result, write_minimal_wav  # noqa: E402


def _make_ogg_converter(ogg_path: str, ogg_bytes: bytes = b"fakeogg") -> AsyncMock:
    """Return a mock AudioConverter that writes ogg_bytes to ogg_path on call."""

    async def _fake_convert(wav_path: Path, out_path: Path) -> None:
        Path(ogg_path).write_bytes(ogg_bytes)

    converter = AsyncMock()
    converter.convert_wav_to_ogg = AsyncMock(side_effect=_fake_convert)
    return converter

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
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    converter = _make_ogg_converter(ogg_path)
    svc = TTSService(TTSConfig(engine="global_engine", voice="global_voice"), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

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
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    converter = _make_ogg_converter(ogg_path)
    svc = TTSService(TTSConfig(engine="global", voice="global_v", language="English"), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    agent_tts = AgentTTSConfig(
        engine="agent_engine", voice="agent_voice", language="French"
    )

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
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
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    converter = _make_ogg_converter(ogg_path)
    svc = TTSService(TTSConfig(), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    agent_tts = AgentTTSConfig(chunked=False)

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        await svc.synthesize("Hello", agent_tts=agent_tts)

    assert captured.get("chunked") is True


@pytest.mark.asyncio
async def test_synthesize_agent_tts_none_uses_global_defaults():
    """synthesize(agent_tts=None) uses global defaults — no regression."""
    captured: dict = {}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_ogg:
        ogg_path = tmp_ogg.name

    converter = _make_ogg_converter(ogg_path)
    svc = TTSService(TTSConfig(engine="global_eng", voice="global_vox"), converter=converter)

    write_minimal_wav(wav_path)

    async def fake_gen(text, **kwargs):
        captured.update(kwargs)
        write_minimal_wav(wav_path)
        return make_chunked_result([wav_path])

    with patch("voicecli.generate_async", new=AsyncMock(side_effect=fake_gen)):
        await svc.synthesize("Hello", agent_tts=None)

    assert captured.get("engine") == "global_eng"
    assert captured.get("voice") == "global_vox"
