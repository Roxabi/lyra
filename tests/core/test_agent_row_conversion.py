"""Tests for agent_row_to_config() -- voice_json deserialization and patterns.

After #346, AgentRow no longer has tts_json/stt_json/persona/i18n_language.
Voice data lives in voice_json = '{"tts": {...}, "stt": {...}}'.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestAgentRowToConfigTTSSTT:
    """agent_row_to_config() must deserialize voice_json into typed config."""

    def _make_row(self, voice_json=None):
        from lyra.core.agent_models import AgentRow

        return AgentRow(
            name="row-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            voice_json=voice_json,
        )

    def test_null_voice_json_produces_none(self):
        from lyra.core.agent_db_loader import agent_row_to_config

        row = self._make_row(voice_json=None)
        agent = agent_row_to_config(row)
        assert agent.voice is None

    def test_tts_json_deserializes_to_agent_voice_config(self):
        from lyra.core.agent_config import AgentTTSConfig
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {
            "tts": {
                "engine": "chatterbox",
                "voice": "en-US-1",
                "chunked": True,
                "chunk_size": 200,
            },
            "stt": {},
        }
        row = self._make_row(voice_json=json.dumps(voice_data))
        agent = agent_row_to_config(row)

        assert agent.voice is not None
        assert isinstance(agent.voice.tts, AgentTTSConfig)
        assert agent.voice.tts.engine == "chatterbox"
        assert agent.voice.tts.voice == "en-US-1"
        assert agent.voice.tts.chunked is True
        assert agent.voice.tts.chunk_size == 200
        # fallback_language bridged into stt.language_fallback
        assert agent.voice.stt.language_fallback == "en"

    def test_stt_json_deserializes_to_agent_voice_config(self):
        from lyra.core.agent_config import AgentSTTConfig
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {
            "tts": {},
            "stt": {
                "language_detection_threshold": 0.75,
                "language_detection_segments": 3,
                "language_fallback": "en",
            },
        }
        row = self._make_row(voice_json=json.dumps(voice_data))
        agent = agent_row_to_config(row)

        assert agent.voice is not None
        assert isinstance(agent.voice.stt, AgentSTTConfig)
        assert agent.voice.stt.language_detection_threshold == 0.75
        assert agent.voice.stt.language_detection_segments == 3
        assert agent.voice.stt.language_fallback == "en"

    def test_both_tts_and_stt_deserialized(self):
        from lyra.core.agent_config import AgentSTTConfig, AgentTTSConfig
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {
            "tts": {"engine": "chatterbox", "voice": "en-GB-2"},
            "stt": {"language_fallback": "fr"},
        }
        row = self._make_row(voice_json=json.dumps(voice_data))
        agent = agent_row_to_config(row)

        assert isinstance(agent.voice.tts, AgentTTSConfig)
        assert agent.voice.tts.engine == "chatterbox"
        assert agent.voice.tts.voice == "en-GB-2"
        assert isinstance(agent.voice.stt, AgentSTTConfig)
        assert agent.voice.stt.language_fallback == "fr"

    def test_empty_voice_json_returns_none(self):
        from lyra.core.agent_db_loader import agent_row_to_config

        row = self._make_row(voice_json="")
        agent = agent_row_to_config(row)
        assert agent.voice is None

    def test_malformed_voice_json_raises(self):
        from lyra.core.agent_db_loader import agent_row_to_config

        row = self._make_row(voice_json="not valid json")
        with pytest.raises(json.JSONDecodeError):
            agent_row_to_config(row)

    def test_tts_json_with_exaggeration_and_cfg_weight(self):
        """T5: voice_json with exaggeration/cfg_weight deserializes to float fields."""
        from lyra.core.agent_db_loader import agent_row_to_config

        voice_data = {"tts": {"exaggeration": 0.7, "cfg_weight": 0.3}, "stt": {}}
        row = self._make_row(voice_json=json.dumps(voice_data))

        agent = agent_row_to_config(row)

        assert agent.voice is not None
        assert agent.voice.tts.exaggeration == pytest.approx(0.7)
        assert agent.voice.tts.cfg_weight == pytest.approx(0.3)


class TestAgentRowToConfigPatterns:
    """agent_row_to_config() deserialises patterns_json into Agent.patterns."""

    def test_patterns_json_bare_url_false(self) -> None:
        from lyra.core.agent_db_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="r",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=json.dumps({"bare_url": False}),
        )

        agent = agent_row_to_config(row)

        assert agent.patterns == {"bare_url": False}

    def test_patterns_json_none_yields_empty(self) -> None:
        from lyra.core.agent_db_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="s",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=None,
        )

        agent = agent_row_to_config(row)

        assert agent.patterns == {}

    def test_patterns_json_non_dict_yields_empty(self) -> None:
        from lyra.core.agent_db_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="t",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=json.dumps(["bare_url"]),
        )

        agent = agent_row_to_config(row)

        assert agent.patterns == {}
