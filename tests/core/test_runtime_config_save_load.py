"""Tests for RuntimeConfig save() / load() (issue #135).

Covers:
  save()  — defaults not persisted, non-defaults written
  load()  — roundtrip, absent file, corrupt file, full-field roundtrip
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lyra.core.runtime_config import RuntimeConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_toml(tmp_path: Path) -> Path:
    return tmp_path / "lyra_runtime.toml"


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
