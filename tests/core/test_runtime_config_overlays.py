"""Tests for RuntimeConfig overlay behaviour (issue #135).

Covers:
  RuntimeConfig dataclass — default values, mutability
  overlay()              — style, language, extra_instructions, model, max_steps,
                           temperature, EffectiveConfig frozen
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from lyra.core.agent import Agent
from lyra.core.agent_config import ModelConfig
from lyra.core.runtime_config import (
    _STYLE_INSTRUCTIONS,
    RuntimeConfig,
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
        llm_config=ModelConfig(model="claude-haiku-4-5-20251001", max_turns=10),
    )


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

    def test_empty_extra_instructions_no_injection(self, base_agent: Agent) -> None:
        # Arrange — default empty string
        rc = RuntimeConfig(extra_instructions="")

        # Act
        effective = rc.overlay(base_agent)

        # Assert — system prompt unchanged from base
        assert effective.system_prompt == base_agent.system_prompt

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
        assert effective.model == base_agent.llm_config.model

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
        assert effective.max_turns == base_agent.llm_config.max_turns

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
