"""Tests for TTSConfig defaults and load_tts_config() env-var loading."""

from __future__ import annotations

import warnings

import pytest

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


class TestLoadTtsConfigDeprecation:
    def test_tts_engine_deprecated_fallback_emits_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.delenv("LYRA_TTS_ENGINE", raising=False)
        monkeypatch.setenv("TTS_ENGINE", "chatterbox")
        # Act
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_tts_config()
        # Assert
        assert cfg.engine == "chatterbox"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 1
        assert "TTS_ENGINE" in str(deprecation_warnings[0].message)

    def test_tts_new_engine_var_wins_over_deprecated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — both vars set; LYRA_TTS_ENGINE should win
        monkeypatch.setenv("LYRA_TTS_ENGINE", "qwen-fast")
        monkeypatch.setenv("TTS_ENGINE", "chatterbox")
        # Act
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_tts_config()
        # Assert
        assert cfg.engine == "qwen-fast"
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings == []
