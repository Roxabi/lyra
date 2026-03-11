"""Tests for RuntimeConfig (issue #135).

Covers:
  RuntimeConfig dataclass — default values, mutability
  overlay()              — style, language, extra_instructions, model, max_steps,
                           temperature, EffectiveConfig frozen
  save() / load()        — roundtrip, absent file, corrupt file
  set_param()            — valid/invalid values for each param, immutability
  reset()                — full reset, per-field reset, unknown key
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from lyra.core.agent import Agent, ModelConfig
from lyra.core.runtime_config import (
    _STYLE_INSTRUCTIONS,
    RuntimeConfig,
    set_param,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_agent() -> Agent:
    return Agent(
        name="test",
        system_prompt="Base prompt",
        memory_namespace="test",
        model_config=ModelConfig(model="claude-haiku-4-5-20251001", max_turns=10),
    )


@pytest.fixture()
def tmp_toml(tmp_path: Path) -> Path:
    return tmp_path / "lyra_runtime.toml"


# ---------------------------------------------------------------------------
# RuntimeConfig dataclass
# ---------------------------------------------------------------------------


class TestRuntimeConfigDefaults:
    """RuntimeConfig() default values match the spec."""

    def test_default_style(self) -> None:
        # Arrange / Act
        rc = RuntimeConfig()

        # Assert
        assert rc.style == "concise"

    def test_default_language(self) -> None:
        rc = RuntimeConfig()
        assert rc.language == "auto"

    def test_default_temperature(self) -> None:
        rc = RuntimeConfig()
        assert rc.temperature == 0.7

    def test_default_model_is_none(self) -> None:
        rc = RuntimeConfig()
        assert rc.model is None

    def test_default_max_steps_is_none(self) -> None:
        rc = RuntimeConfig()
        assert rc.max_steps is None

    def test_default_extra_instructions_empty(self) -> None:
        rc = RuntimeConfig()
        assert rc.extra_instructions == ""

    def test_fields_are_mutable(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act — should not raise
        rc.style = "detailed"
        rc.temperature = 0.5

        # Assert
        assert rc.style == "detailed"
        assert rc.temperature == 0.5


# ---------------------------------------------------------------------------
# overlay()
# ---------------------------------------------------------------------------


class TestOverlayStyle:
    """overlay() injects style text according to the style field."""

    def test_concise_style_no_style_text_injected(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(style="concise")

        # Act
        effective = rc.overlay(base_agent)

        # Assert — system_prompt == base (style text for concise is not injected)
        assert effective.system_prompt == "Base prompt"

    def test_detailed_style_appended(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(style="detailed")

        # Act
        effective = rc.overlay(base_agent)

        # Assert — style instruction is appended with double newline
        assert effective.system_prompt.startswith("Base prompt\n\n")
        assert _STYLE_INSTRUCTIONS["detailed"] in effective.system_prompt

    def test_technical_style_appended(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(style="technical")

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert _STYLE_INSTRUCTIONS["technical"] in effective.system_prompt

    def test_friendly_style_appended(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(style="friendly")

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert _STYLE_INSTRUCTIONS["friendly"] in effective.system_prompt


class TestOverlayLanguage:
    """overlay() appends language instruction when language != 'auto'."""

    def test_language_fr_appended(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(language="fr")

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert "Reply in fr." in effective.system_prompt

    def test_language_auto_nothing_appended(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(language="auto")

        # Act
        effective = rc.overlay(base_agent)

        # Assert — no language fragment added
        assert "Reply in" not in effective.system_prompt


class TestOverlayExtraInstructions:
    """overlay() appends extra_instructions last."""

    def test_extra_instructions_appended_last(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(extra_instructions="Always be brief.")

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.system_prompt.endswith("Always be brief.")

    def test_all_three_combined(self, base_agent: Agent) -> None:
        # Arrange — style=detailed, language=fr, extra="foo"
        rc = RuntimeConfig(style="detailed", language="fr", extra_instructions="foo")

        # Act
        effective = rc.overlay(base_agent)

        # Assert — order: base + detailed_text + language + extra
        prompt = effective.system_prompt
        assert prompt.startswith("Base prompt\n\n")
        assert _STYLE_INSTRUCTIONS["detailed"] in prompt
        assert "Reply in fr." in prompt
        idx_style = prompt.index(_STYLE_INSTRUCTIONS["detailed"])
        idx_lang = prompt.index("Reply in fr.")
        idx_extra = prompt.index("foo")
        assert idx_style < idx_lang < idx_extra


class TestOverlayModel:
    """overlay() resolves effective model from RuntimeConfig or agent defaults."""

    def test_model_none_uses_agent_model(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(model=None)

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.model == base_agent.model_config.model

    def test_model_override(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(model="claude-opus-4-6")

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.model == "claude-opus-4-6"


class TestOverlayMaxSteps:
    """overlay() resolves effective max_turns from RuntimeConfig or agent defaults."""

    def test_max_steps_none_uses_agent_max_turns(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(max_steps=None)

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.max_turns == base_agent.model_config.max_turns

    def test_max_steps_override(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(max_steps=5)

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.max_turns == 5


class TestOverlayTemperature:
    """overlay() forwards temperature to EffectiveConfig."""

    def test_temperature_forwarded(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig(temperature=0.3)

        # Act
        effective = rc.overlay(base_agent)

        # Assert
        assert effective.temperature == 0.3


class TestEffectiveConfigFrozen:
    """EffectiveConfig is frozen — mutation raises FrozenInstanceError."""

    def test_effective_config_is_frozen(self, base_agent: Agent) -> None:
        # Arrange
        rc = RuntimeConfig()
        effective = rc.overlay(base_agent)

        # Act / Assert
        with pytest.raises(FrozenInstanceError):
            effective.model = "something-else"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# save() / load()
# ---------------------------------------------------------------------------


class TestSaveLoad:
    """save() persists non-defaults; load() restores them; missing/corrupt graceful."""

    def test_save_defaults_file_empty_or_no_fields(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        rc.save(tmp_toml)

        # Assert — file written; defaults not persisted (nothing to read back other
        # than what was changed from default)
        content = tmp_toml.read_text()
        # Either empty file or no field keys present
        assert "style" not in content
        assert "language" not in content
        assert "temperature" not in content

    def test_save_non_default_style(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig(style="detailed")

        # Act
        rc.save(tmp_toml)

        # Assert
        content = tmp_toml.read_text()
        assert 'style = "detailed"' in content
        assert "language" not in content

    def test_save_non_default_temperature(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig(temperature=0.3)

        # Act
        rc.save(tmp_toml)

        # Assert
        content = tmp_toml.read_text()
        assert "temperature = 0.3" in content

    def test_save_multiple_non_defaults(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig(style="technical", temperature=0.2, language="de")

        # Act
        rc.save(tmp_toml)

        # Assert
        content = tmp_toml.read_text()
        assert 'style = "technical"' in content
        assert "temperature = 0.2" in content
        assert 'language = "de"' in content

    def test_load_written_file_roundtrips(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig(style="friendly", temperature=0.1, language="es")
        rc.save(tmp_toml)

        # Act
        loaded = RuntimeConfig.load(tmp_toml)

        # Assert
        assert loaded.style == "friendly"
        assert loaded.temperature == 0.1
        assert loaded.language == "es"

    def test_load_absent_file_returns_default(self, tmp_toml: Path) -> None:
        # Arrange — file does not exist
        assert not tmp_toml.exists()

        # Act
        loaded = RuntimeConfig.load(tmp_toml)

        # Assert
        assert loaded == RuntimeConfig()

    def test_load_corrupt_file_returns_default(
        self, tmp_toml: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — write invalid TOML
        tmp_toml.write_text("not valid toml ][")

        # Act
        import logging

        with caplog.at_level(logging.WARNING, logger="lyra.core.runtime_config"):
            loaded = RuntimeConfig.load(tmp_toml)

        # Assert — returns default, logs a warning
        assert loaded == RuntimeConfig()
        assert len(caplog.records) > 0

    def test_save_load_full_roundtrip(self, tmp_toml: Path) -> None:
        # Arrange
        rc = RuntimeConfig(
            style="detailed",
            language="fr",
            temperature=0.4,
            model="claude-opus-4-6",
            max_steps=7,
            extra_instructions="Be succinct.",
        )

        # Act
        rc.save(tmp_toml)
        loaded = RuntimeConfig.load(tmp_toml)

        # Assert
        assert loaded.style == rc.style
        assert loaded.language == rc.language
        assert loaded.temperature == rc.temperature
        assert loaded.model == rc.model
        assert loaded.max_steps == rc.max_steps
        assert loaded.extra_instructions == rc.extra_instructions


# ---------------------------------------------------------------------------
# set_param()
# ---------------------------------------------------------------------------


class TestSetParam:
    """set_param() returns a new RuntimeConfig with one field changed."""

    def test_valid_style_detailed(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "style", "detailed")

        # Assert
        assert updated.style == "detailed"

    def test_invalid_style_raises_value_error(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError, match="Invalid style"):
            set_param(rc, "style", "nonexistent_style")

    def test_valid_temperature_float(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "temperature", "0.3")

        # Assert
        assert updated.temperature == pytest.approx(0.3)

    def test_temperature_out_of_range_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "temperature", "1.5")

    def test_temperature_non_float_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "temperature", "abc")

    def test_valid_max_steps_int(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "max_steps", "5")

        # Assert
        assert updated.max_steps == 5

    def test_max_steps_non_int_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "max_steps", "3.5")

    def test_model_none_lowercase(self) -> None:
        # Arrange
        rc = RuntimeConfig(model="claude-haiku-4-5-20251001")

        # Act
        updated = set_param(rc, "model", "none")

        # Assert
        assert updated.model is None

    def test_model_none_uppercase(self) -> None:
        # Arrange — the implementation treats only "" and lowercase "none" as None;
        # title-case "None" is stored as-is (implementation detail).
        rc = RuntimeConfig(model="claude-haiku-4-5-20251001")

        # Act
        updated = set_param(rc, "model", "None")

        # Assert — "None" (title-case) is NOT normalised;
        # model is set to the string "None"
        assert updated.model == "None"

    def test_unknown_key_raises_value_error(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError, match="Unknown config key"):
            set_param(rc, "bogus_key", "value")

    def test_set_param_returns_new_instance(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "style", "detailed")

        # Assert — original is unchanged
        assert rc.style == "concise"
        assert updated is not rc


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


class TestReset:
    """reset() restores fields to their defaults."""

    def test_reset_no_args_returns_default(self) -> None:
        # Arrange
        rc = RuntimeConfig(style="detailed", temperature=0.1)

        # Act
        result = rc.reset(rc)

        # Assert
        assert result == RuntimeConfig()

    def test_reset_style_resets_to_concise(self) -> None:
        # Arrange
        rc = RuntimeConfig(style="friendly", temperature=0.2, language="de")

        # Act
        result = rc.reset(rc, "style")

        # Assert — style reset, others unchanged
        assert result.style == "concise"
        assert result.temperature == 0.2
        assert result.language == "de"

    def test_reset_temperature_to_default(self) -> None:
        # Arrange
        rc = RuntimeConfig(temperature=0.1, style="technical")

        # Act
        result = rc.reset(rc, "temperature")

        # Assert
        assert result.temperature == 0.7
        assert result.style == "technical"

    def test_reset_model_to_none(self) -> None:
        # Arrange
        rc = RuntimeConfig(model="claude-opus-4-6")

        # Act
        result = rc.reset(rc, "model")

        # Assert
        assert result.model is None

    def test_reset_with_none_current_returns_default(self) -> None:
        # Arrange / Act
        result = RuntimeConfig.reset(None, "style")

        # Assert
        assert result == RuntimeConfig()

    def test_reset_unknown_key_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            rc.reset(rc, "unknown_key")
