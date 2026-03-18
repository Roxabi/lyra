"""Tests for AgentTTSConfig/AgentSTTConfig dataclasses, TOML parsing, and STT overlay.
"""

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
