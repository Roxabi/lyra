"""Tests for AgentTTSConfig/AgentSTTConfig dataclasses, AgentRow voice_json
deserialization, and STT overlay.

The TOML loading path (load_agent_config) was removed in #346.
Tests that exercised TOML loading have been rewritten to use AgentRow +
agent_row_to_config.
"""

from __future__ import annotations

import json

import pytest


class TestAgentTTSConfig:
    """T08 -- AgentTTSConfig dataclass must exist with all-optional fields."""

    def test_agent_tts_config_all_optional(self):
        from lyra.core.agent_config import AgentTTSConfig

        cfg = AgentTTSConfig()
        assert cfg.engine is None
        assert cfg.voice is None
        assert cfg.language is None
        assert cfg.accent is None

    def test_agent_stt_config_all_optional(self):
        from lyra.core.agent_config import AgentSTTConfig

        cfg = AgentSTTConfig()
        assert cfg.language_detection_threshold is None
        assert cfg.language_detection_segments is None
        assert cfg.language_fallback is None


class TestAgentRowToConfigTTSSTT:
    """T09 -- agent_row_to_config parses voice_json into Agent.voice."""

    def _make_row(self, voice_json=None):
        from lyra.core.agent_models import AgentRow

        return AgentRow(
            name="x",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            voice_json=voice_json,
        )

    def test_voice_json_with_tts_section(self):
        """voice_json with tts data is parsed into Agent.voice.tts."""
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {"tts": {"engine": "qwen-fast", "voice": "Ono_Anna", "language": "French"}, "stt": {}}
        row = self._make_row(voice_json=json.dumps(voice_data))
        agent = agent_row_to_config(row)
        assert agent.voice is not None
        assert agent.voice.tts.engine == "qwen-fast"
        assert agent.voice.tts.voice == "Ono_Anna"
        assert agent.voice.tts.language == "French"

    def test_voice_json_with_stt_section(self):
        """voice_json with stt data is parsed into Agent.voice.stt."""
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {"tts": {}, "stt": {"language_detection_threshold": 0.9, "language_fallback": "en"}}
        row = self._make_row(voice_json=json.dumps(voice_data))
        agent = agent_row_to_config(row)
        assert agent.voice is not None
        assert agent.voice.stt.language_detection_threshold == 0.9
        assert agent.voice.stt.language_fallback == "en"
        assert agent.voice.stt.language_detection_segments is None

    def test_no_voice_json_produces_none(self):
        """Agent without voice_json -> .voice is None."""
        from lyra.core.agent_db_loader import agent_row_to_config

        row = self._make_row(voice_json=None)
        agent = agent_row_to_config(row)
        assert agent.voice is None


class TestAgentRowToConfigTTSNewFields:
    """T6 -- agent_row_to_config parses exaggeration and cfg_weight from voice_json."""

    def test_tts_exaggeration_and_cfg_weight(self) -> None:
        """voice_json with exaggeration + cfg_weight is parsed into float fields."""
        from lyra.core.agent_db_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        voice_data = {"tts": {"exaggeration": 0.7, "cfg_weight": 0.3}, "stt": {}}
        row = AgentRow(
            name="x",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            voice_json=json.dumps(voice_data),
        )
        agent = agent_row_to_config(row)

        assert agent.voice is not None
        assert agent.voice.tts.exaggeration == pytest.approx(0.7)
        assert agent.voice.tts.cfg_weight == pytest.approx(0.3)
        assert agent.voice.tts.engine is None  # not set -- defaults preserved


# ---------------------------------------------------------------------------
# SC-8 -- apply_agent_stt_overlay helper
# ---------------------------------------------------------------------------


class TestApplyAgentSTTOverlay:
    """SC-8 -- apply_agent_stt_overlay merges AgentSTTConfig into STTConfig."""

    def test_none_agent_stt_returns_stt_cfg_unchanged(self):
        from lyra.bootstrap.agent_factory import apply_agent_stt_overlay
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(model_size="large-v3-turbo")
        result = apply_agent_stt_overlay(None, stt_cfg)
        assert result is stt_cfg

    def test_non_none_fields_overwrite(self):
        from lyra.bootstrap.agent_factory import apply_agent_stt_overlay
        from lyra.core.agent_config import AgentSTTConfig
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(model_size="large-v3-turbo")
        agent_stt = AgentSTTConfig(
            language_detection_threshold=0.9,
            language_fallback="en",
        )
        result = apply_agent_stt_overlay(agent_stt, stt_cfg)
        assert result.language_detection_threshold == 0.9
        assert result.language_fallback == "en"
        assert result.language_detection_segments is None  # unchanged

    def test_none_fields_leave_stt_cfg_unchanged(self):
        from lyra.bootstrap.agent_factory import apply_agent_stt_overlay
        from lyra.core.agent_config import AgentSTTConfig
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(
            model_size="large-v3-turbo",
            language_detection_threshold=0.8,
            language_detection_segments=3,
            language_fallback="fr",
        )
        agent_stt = AgentSTTConfig()  # all fields None
        result = apply_agent_stt_overlay(agent_stt, stt_cfg)
        assert result.language_detection_threshold == 0.8
        assert result.language_detection_segments == 3
        assert result.language_fallback == "fr"
