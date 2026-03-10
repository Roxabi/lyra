"""Tests for _load_circuit_config() in lyra.__main__ (issue #104, SC-16).

Covers:
  SC-16: TOML-driven circuit config with defaults and per-service overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLoadCircuitConfigDefaults:
    """Missing config file → 4 CBs with default thresholds (SC-16)."""

    def test_load_circuit_config_uses_defaults_when_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: Missing lyra.toml → 4 CBs with failure_threshold=5, recovery_timeout=60."""  # noqa: E501
        # Arrange — point LYRA_CONFIG at a nonexistent file
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))

        # Act
        from lyra.__main__ import (  # noqa: PLC0415
            _load_circuit_config,
            _load_raw_config,
        )

        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert — all four default services present with correct defaults
        for name in ("anthropic", "telegram", "discord", "hub"):
            cb = registry[name]
            assert cb.failure_threshold == 5, (
                f"{name}: expected failure_threshold=5, got {cb.failure_threshold}"
            )
            assert cb.recovery_timeout == 60, (
                f"{name}: expected recovery_timeout=60, got {cb.recovery_timeout}"
            )

        assert admin_ids == set()

    def test_load_circuit_config_uses_defaults_when_env_not_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: LYRA_CONFIG unset and no lyra.toml in cwd → defaults returned."""
        # Arrange — unset env var and ensure cwd has no lyra.toml
        monkeypatch.delenv("LYRA_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        # Act
        from lyra.__main__ import (  # noqa: PLC0415
            _load_circuit_config,
            _load_raw_config,
        )

        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert — defaults apply
        for name in ("anthropic", "telegram", "discord", "hub"):
            cb = registry[name]
            assert cb.failure_threshold == 5
            assert cb.recovery_timeout == 60

        assert admin_ids == set()


class TestLoadCircuitConfigTomlOverrides:
    """TOML values override defaults per-service (SC-16)."""

    def test_load_circuit_config_overrides_from_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: TOML config overrides anthropic thresholds; other services keep defaults."""  # noqa: E501
        # Arrange
        config = tmp_path / "lyra.toml"
        config.write_text(
            "[circuit_breaker.anthropic]\n"
            "failure_threshold = 2\n"
            "recovery_timeout = 30\n"
            "[admin]\n"
            "user_ids = ['telegram:tg:user:42']\n"
        )
        monkeypatch.setenv("LYRA_CONFIG", str(config))

        # Act
        from lyra.__main__ import (  # noqa: PLC0415
            _load_circuit_config,
            _load_raw_config,
        )

        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert — anthropic overridden
        assert registry["anthropic"].failure_threshold == 2
        assert registry["anthropic"].recovery_timeout == 30

        # Assert — other services keep defaults
        assert registry["telegram"].failure_threshold == 5
        assert registry["telegram"].recovery_timeout == 60
        assert registry["discord"].failure_threshold == 5
        assert registry["hub"].failure_threshold == 5

        # Assert — admin list parsed
        assert "telegram:tg:user:42" in admin_ids

    def test_load_circuit_config_multiple_admin_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: Multiple admin user_ids parsed into a set."""
        # Arrange
        config = tmp_path / "lyra.toml"
        config.write_text(
            "[admin]\nuser_ids = ['telegram:tg:user:1', 'discord:dc:user:2']\n"
        )
        monkeypatch.setenv("LYRA_CONFIG", str(config))

        # Act
        from lyra.__main__ import (  # noqa: PLC0415
            _load_circuit_config,
            _load_raw_config,
        )

        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert
        assert "telegram:tg:user:1" in admin_ids
        assert "discord:dc:user:2" in admin_ids
        assert len(admin_ids) == 2

    def test_load_circuit_config_partial_override_keeps_other_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: Only recovery_timeout overridden → failure_threshold stays default."""
        # Arrange
        config = tmp_path / "lyra.toml"
        config.write_text("[circuit_breaker.hub]\nrecovery_timeout = 120\n")
        monkeypatch.setenv("LYRA_CONFIG", str(config))

        # Act
        from lyra.__main__ import (  # noqa: PLC0415
            _load_circuit_config,
            _load_raw_config,
        )

        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert — hub recovery_timeout overridden but failure_threshold stays 5
        assert registry["hub"].recovery_timeout == 120
        assert registry["hub"].failure_threshold == 5

        # Assert — no admin ids
        assert admin_ids == set()
