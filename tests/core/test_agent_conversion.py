"""Tests for agent row-to-config conversion, TTS/STT config, and pattern parsing."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestAgentTTSConfig:
    """T08 — AgentTTSConfig dataclass must exist with all-optional fields."""

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


class TestLoadAgentConfigTTSSTT:
    """T09 — load_agent_config parses [tts]/[stt] TOML sections."""

    def test_load_agent_config_parses_tts_section(self, tmp_path: Path, monkeypatch):
        """[tts] section in agent TOML is parsed into AgentTTSConfig."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[tts]
engine = "qwen-fast"
voice = "Ono_Anna"
language = "French"
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.tts is not None
        assert agent.tts.engine == "qwen-fast"
        assert agent.tts.voice == "Ono_Anna"
        assert agent.tts.language == "French"

    def test_load_agent_config_parses_stt_section(self, tmp_path: Path, monkeypatch):
        """[stt] section in agent TOML is parsed into AgentSTTConfig."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[stt]
language_detection_threshold = 0.9
language_fallback = "en"
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.stt is not None
        assert agent.stt.language_detection_threshold == 0.9
        assert agent.stt.language_fallback == "en"
        assert agent.stt.language_detection_segments is None

    def test_load_agent_config_missing_tts_stt_sections(
        self, tmp_path: Path, monkeypatch
    ):
        """Agent without [tts]/[stt] sections -> .tts and .stt are None."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.tts is None
        assert agent.stt is None


class TestLoadAgentConfigTTSNewFields:
    """T6 — load_agent_config parses exaggeration and cfg_weight from [tts] TOML."""

    def test_tts_exaggeration_and_cfg_weight_from_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tts] section with exaggeration + cfg_weight is parsed into float fields."""
        # Arrange
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[tts]
exaggeration = 0.7
cfg_weight = 0.3
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        # Act
        agent = load_agent_config("x", agents_dir=tmp_path)

        # Assert
        assert agent.tts is not None
        assert agent.tts.exaggeration == pytest.approx(0.7)
        assert agent.tts.cfg_weight == pytest.approx(0.3)
        assert agent.tts.engine is None  # not set — defaults preserved


# ---------------------------------------------------------------------------
# SC-8 — apply_agent_stt_overlay helper
# ---------------------------------------------------------------------------


class TestApplyAgentSTTOverlay:
    """SC-8 — apply_agent_stt_overlay merges AgentSTTConfig into STTConfig."""

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


class TestAgentRowToConfigTTSSTT:
    """agent_row_to_config() must deserialize tts_json / stt_json into typed config."""

    def _make_row(self, tts_json=None, stt_json=None):
        from lyra.core.agent_store import AgentRow

        return AgentRow(
            name="row-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            tts_json=tts_json,
            stt_json=stt_json,
        )

    def test_null_tts_stt_produces_none(self):
        from lyra.core.agent_loader import agent_row_to_config

        row = self._make_row(tts_json=None, stt_json=None)
        agent = agent_row_to_config(row)
        assert agent.tts is None
        assert agent.stt is None

    def test_tts_json_deserializes_to_agent_tts_config(self):
        import json

        from lyra.core.agent_config import AgentTTSConfig
        from lyra.core.agent_loader import agent_row_to_config

        tts_data = {
            "engine": "chatterbox",
            "voice": "en-US-1",
            "chunked": True,
            "chunk_size": 200,
        }
        row = self._make_row(tts_json=json.dumps(tts_data), stt_json=None)
        agent = agent_row_to_config(row)

        assert agent.tts is not None
        assert isinstance(agent.tts, AgentTTSConfig)
        assert agent.tts.engine == "chatterbox"
        assert agent.tts.voice == "en-US-1"
        assert agent.tts.chunked is True
        assert agent.tts.chunk_size == 200
        # #343: stt gets a default AgentSTTConfig with fallback_language bridged
        assert agent.stt is not None
        assert agent.stt.language_fallback == "en"

    def test_stt_json_deserializes_to_agent_stt_config(self):
        import json

        from lyra.core.agent_config import AgentSTTConfig
        from lyra.core.agent_loader import agent_row_to_config

        stt_data = {
            "language_detection_threshold": 0.75,
            "language_detection_segments": 3,
            "language_fallback": "en",
        }
        row = self._make_row(tts_json=None, stt_json=json.dumps(stt_data))
        agent = agent_row_to_config(row)

        assert agent.stt is not None
        assert isinstance(agent.stt, AgentSTTConfig)
        assert agent.stt.language_detection_threshold == 0.75
        assert agent.stt.language_detection_segments == 3
        assert agent.stt.language_fallback == "en"
        assert agent.tts is None

    def test_both_tts_and_stt_json_deserialized(self):
        import json

        from lyra.core.agent_config import AgentSTTConfig, AgentTTSConfig
        from lyra.core.agent_loader import agent_row_to_config

        tts_data = {"engine": "chatterbox", "voice": "en-GB-2"}
        stt_data = {"language_fallback": "fr"}
        row = self._make_row(
            tts_json=json.dumps(tts_data), stt_json=json.dumps(stt_data)
        )
        agent = agent_row_to_config(row)

        assert isinstance(agent.tts, AgentTTSConfig)
        assert agent.tts.engine == "chatterbox"
        assert agent.tts.voice == "en-GB-2"
        assert isinstance(agent.stt, AgentSTTConfig)
        assert agent.stt.language_fallback == "fr"

    def test_empty_string_tts_json_returns_none(self):
        from lyra.core.agent_loader import agent_row_to_config

        row = self._make_row(tts_json="", stt_json="")
        agent = agent_row_to_config(row)
        assert agent.tts is None
        assert agent.stt is None

    def test_malformed_tts_json_raises(self):
        import json

        from lyra.core.agent_loader import agent_row_to_config

        row = self._make_row(tts_json="not valid json")
        with pytest.raises(json.JSONDecodeError):
            agent_row_to_config(row)

    def test_tts_json_with_exaggeration_and_cfg_weight(self):
        """T5: tts_json with exaggeration/cfg_weight deserializes to float fields."""
        import json

        from lyra.core.agent_loader import agent_row_to_config

        # Arrange
        tts_data = {"exaggeration": 0.7, "cfg_weight": 0.3}
        row = self._make_row(tts_json=json.dumps(tts_data))

        # Act
        agent = agent_row_to_config(row)

        # Assert
        assert agent.tts is not None
        assert agent.tts.exaggeration == pytest.approx(0.7)
        assert agent.tts.cfg_weight == pytest.approx(0.3)


class TestPatternsParsedFromToml:
    """load_agent_config() parses [patterns] TOML section into Agent.patterns."""

    def test_patterns_bare_url_false_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — TOML with [patterns] bare_url = false
        toml_content = """
[agent]
name = "p"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[patterns]
bare_url = false
"""
        (tmp_path / "p.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        # Act
        agent = load_agent_config("p", agents_dir=tmp_path)

        # Assert
        assert agent.patterns == {"bare_url": False}

    def test_patterns_absent_defaults_to_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — TOML with no [patterns] section
        toml_content = """
[agent]
name = "q"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5
"""
        (tmp_path / "q.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent_loader import load_agent_config

        # Act
        agent = load_agent_config("q", agents_dir=tmp_path)

        # Assert
        assert agent.patterns == {}


class TestAgentRowToConfigPatterns:
    """agent_row_to_config() deserialises patterns_json into Agent.patterns."""

    def test_patterns_json_bare_url_false(self) -> None:
        # Arrange
        import json

        from lyra.core.agent_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="r",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=json.dumps({"bare_url": False}),
        )

        # Act
        agent = agent_row_to_config(row)

        # Assert
        assert agent.patterns == {"bare_url": False}

    def test_patterns_json_none_yields_empty(self) -> None:
        # Arrange
        from lyra.core.agent_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="s",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=None,
        )

        # Act
        agent = agent_row_to_config(row)

        # Assert
        assert agent.patterns == {}

    def test_patterns_json_non_dict_yields_empty(self) -> None:
        # Arrange — corrupted patterns_json (list instead of dict)
        import json

        from lyra.core.agent_loader import agent_row_to_config
        from lyra.core.agent_models import AgentRow

        row = AgentRow(
            name="t",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            patterns_json=json.dumps(["bare_url"]),
        )

        # Act
        agent = agent_row_to_config(row)

        # Assert — falls back to empty dict, no exception
        assert agent.patterns == {}
