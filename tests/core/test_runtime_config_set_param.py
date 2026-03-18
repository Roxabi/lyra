"""Tests for RuntimeConfig set_param() (issue #135).

Covers:
  set_param() — valid/invalid values for each param, immutability, unknown key
"""

from __future__ import annotations

import pytest

from lyra.core.runtime_config import (
    RuntimeConfig,
    set_param,
)

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

    def test_temperature_min_boundary_accepted(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "temperature", "0.0")

        # Assert — 0.0 is the inclusive lower boundary
        assert updated.temperature == pytest.approx(0.0)

    def test_temperature_max_boundary_accepted(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "temperature", "1.0")

        # Assert — 1.0 is the inclusive upper boundary
        assert updated.temperature == pytest.approx(1.0)

    def test_temperature_below_min_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "temperature", "-0.001")

    def test_temperature_above_max_raises(self) -> None:
        # Arrange
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "temperature", "1.001")

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

    def test_max_steps_zero_raises(self) -> None:
        # Arrange — 0 would produce range(0) in process(), returning no response.
        # Policy: reject 0 to prevent silent no-op turns.
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "max_steps", "0")

    def test_max_steps_negative_raises(self) -> None:
        # Arrange — negative values produce range(0) like 0.
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "max_steps", "-1")

    def test_max_steps_upper_bound_accepted(self) -> None:
        # Arrange — 50 is the max allowed value
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "max_steps", "50")

        # Assert
        assert updated.max_steps == 50

    def test_max_steps_above_upper_bound_raises(self) -> None:
        # Arrange — 51 exceeds the 50-turn ceiling
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "max_steps", "51")

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
        # Arrange — case-insensitive: "None", "NONE", "none" all clear the model.
        rc = RuntimeConfig(model="claude-haiku-4-5-20251001")

        # Act
        updated = set_param(rc, "model", "None")

        # Assert — title-case "None" is normalised to Python None.
        assert updated.model is None

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

    def test_language_two_char_min_boundary_accepted(self) -> None:
        # Arrange — 2 chars is the minimum valid language code
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "language", "fr")

        # Assert
        assert updated.language == "fr"

    def test_language_eight_char_max_boundary_accepted(self) -> None:
        # Arrange — 8 chars is the maximum valid language code
        rc = RuntimeConfig()

        # Act
        updated = set_param(rc, "language", "zhtwblah")  # 8-char code

        # Assert
        assert updated.language == "zhtwblah"

    def test_language_one_char_too_short_raises(self) -> None:
        # Arrange — 1 char is below the 2-char minimum
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "language", "f")

    def test_language_nine_char_too_long_raises(self) -> None:
        # Arrange — 9 chars exceeds the 8-char maximum
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "language", "abcdefghi")

    def test_language_auto_accepted(self) -> None:
        # Arrange — "auto" is the special sentinel value
        rc = RuntimeConfig(language="fr")

        # Act
        updated = set_param(rc, "language", "auto")

        # Assert
        assert updated.language == "auto"

    def test_language_uppercase_rejected(self) -> None:
        # Arrange — regex requires lowercase only
        rc = RuntimeConfig()

        # Act / Assert
        with pytest.raises(ValueError):
            set_param(rc, "language", "FR")
