"""Tests for RuntimeConfig reset() (issue #135).

Covers:
  reset() — full reset, per-field reset, None current, unknown key
"""

from __future__ import annotations

import pytest

from lyra.core.runtime_config import RuntimeConfig

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
