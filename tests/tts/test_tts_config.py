"""Tests for TTSConfig defaults and load_tts_config() env-var loading."""

from __future__ import annotations

from lyra.tts import (
    TTSConfig,
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
    monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)
    monkeypatch.delenv("LYRA_TTS_VOICE", raising=False)
    monkeypatch.delenv("LYRA_TTS_LANGUAGE", raising=False)
    cfg = load_tts_config()
    assert cfg.engine is None
    assert cfg.voice is None
    assert cfg.language is None


def test_load_tts_config_from_env(monkeypatch):
    monkeypatch.setenv("LYRA_TTS_ENGINE", "qwen")
    monkeypatch.setenv("LYRA_TTS_VOICE", "mia")
    monkeypatch.setenv("LYRA_TTS_LANGUAGE", "English")
    cfg = load_tts_config()
    assert cfg.engine == "qwen"
    assert cfg.voice == "mia"
    assert cfg.language == "English"
