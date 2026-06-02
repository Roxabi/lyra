"""Tests for warn_deprecated_bot_sections + _load_raw_config deprecation call.

Verifies that _load_raw_config emits a single WARNING when legacy TOML bot
sections are present and stays silent on clean configs.

Contract:
  - Function: warn_deprecated_bot_sections(raw: dict)
  - Module: factory.bootstrap.factory.config
  - Logger: factory.bootstrap.factory.config (module-level `log`)
  - Dedup flag: _bot_sections_deprecation_warned (module-level bool, initially False)
  - Log level: WARNING
  - Message substring: "TOML bot sections are deprecated"
  - Detected sections: telegram.bots, discord.bots, auth.telegram_bots,
    auth.discord_bots
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

LOGGER_NAME = "factory.bootstrap.factory.config.config_deprecation"
DEPRECATED_SUBSTRING = "TOML bot sections are deprecated"
FLAG_PATH = "factory.bootstrap.factory.config.config_deprecation._bot_sections_deprecation_warned"  # noqa: E501


class TestWarnOnLegacyBotSections:
    """_load_raw_config emits exactly one WARNING when legacy bot sections are
    present."""

    def test_warns_on_legacy_telegram_bots_section(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exactly one WARNING containing the deprecation substring when
        [[telegram.bots]] exists."""
        # Arrange
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[telegram]\n[[telegram.bots]]\ntoken = "fake-token"\n',
            encoding="utf-8",
        )
        # Reset dedup flag so the warning fires even if another test ran first
        monkeypatch.setattr(FLAG_PATH, False, raising=False)

        from factory.bootstrap.factory.config import _load_raw_config

        # Act
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _load_raw_config(str(config_file))

        # Assert — exactly one record with the deprecation substring
        matching = [r for r in caplog.records if DEPRECATED_SUBSTRING in r.getMessage()]
        assert len(matching) == 1, (
            f"Expected exactly 1 deprecation WARNING, got {len(matching)}. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )
        assert matching[0].levelno == logging.WARNING

    def test_no_warning_on_clean_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No deprecation WARNING when the config contains no legacy bot sections."""
        # Arrange
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[some]\nkey = "value"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(FLAG_PATH, False, raising=False)

        from factory.bootstrap.factory.config import _load_raw_config

        # Act
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _load_raw_config(str(config_file))

        # Assert — zero records with the deprecation substring
        matching = [r for r in caplog.records if DEPRECATED_SUBSTRING in r.getMessage()]
        assert len(matching) == 0, (
            f"Expected 0 deprecation WARNINGs on clean config, got {len(matching)}. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_warns_only_once_across_two_loads(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Deprecation WARNING fires exactly once even when _load_raw_config is
        called twice.

        The module-global _bot_sections_deprecation_warned flag must suppress the
        second emission.  If the flag logic is removed the count becomes 2 and this
        test fails — proving the dedup is load-bearing.
        """
        # Arrange
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[telegram]\n[[telegram.bots]]\ntoken = "fake-token"\n',
            encoding="utf-8",
        )
        # Reset dedup flag to False so the first call fires the warning
        monkeypatch.setattr(FLAG_PATH, False, raising=False)

        from factory.bootstrap.factory.config import _load_raw_config

        # Act — call TWICE with the same path; module flag persists between calls
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _load_raw_config(str(config_file))
            _load_raw_config(str(config_file))

        # Assert — exactly 1 record, not 2
        matching = [r for r in caplog.records if DEPRECATED_SUBSTRING in r.getMessage()]
        assert len(matching) == 1, (
            f"Expected exactly 1 deprecation WARNING across two loads,"
            f" got {len(matching)}. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )
