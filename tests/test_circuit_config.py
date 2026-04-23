"""Tests for bootstrap config loaders (issue #104, SC-16; issue #317).

Covers:
  SC-16: TOML-driven circuit config with defaults and per-service overrides.
  #317: _load_cli_pool_config defaults and TOML overrides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import lyra.bootstrap.factory.config as config_mod
from lyra.bootstrap.factory.config import (
    _load_circuit_config,
    _load_raw_config,
)


class TestLoadCircuitConfigDefaults:
    """Missing config file → 4 CBs with default thresholds (SC-16)."""

    def test_load_circuit_config_uses_defaults_when_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-16: Missing lyra.toml → 4 CBs with failure_threshold=5, recovery_timeout=60."""  # noqa: E501
        # Arrange — point LYRA_CONFIG at a nonexistent file
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))
        monkeypatch.setattr(
            config_mod,
            "_validate_config_path",
            lambda path_str: str(Path(path_str).resolve()),
        )

        # Act
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
        monkeypatch.setattr(
            config_mod,
            "_validate_config_path",
            lambda path_str: str(Path(path_str).resolve()),
        )

        # Act
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
        monkeypatch.setattr(
            config_mod,
            "_validate_config_path",
            lambda path_str: str(Path(path_str).resolve()),
        )

        # Act
        raw = _load_raw_config()
        _, admin_ids = _load_circuit_config(raw)

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
        monkeypatch.setattr(
            config_mod,
            "_validate_config_path",
            lambda path_str: str(Path(path_str).resolve()),
        )

        # Act
        raw = _load_raw_config()
        registry, admin_ids = _load_circuit_config(raw)

        # Assert — hub recovery_timeout overridden but failure_threshold stays 5
        assert registry["hub"].recovery_timeout == 120
        assert registry["hub"].failure_threshold == 5

        # Assert — no admin ids
        assert admin_ids == set()


# ---------------------------------------------------------------------------
# _load_cli_pool_config (#317)
# ---------------------------------------------------------------------------


class TestLoadCliPoolConfig:
    """#317: _load_cli_pool_config reads [cli_pool] section with defaults."""

    def test_defaults_when_section_missing(self) -> None:
        """SC-1: Missing [cli_pool] → hardcoded defaults preserved."""
        from lyra.bootstrap.factory.config import _load_cli_pool_config

        result = _load_cli_pool_config({})
        assert result.idle_ttl == 1200
        assert result.default_timeout == 1200
        assert result.turn_timeout is None

    def test_overrides_from_toml(self) -> None:
        """SC-1: TOML values override defaults."""
        from lyra.bootstrap.factory.config import _load_cli_pool_config

        raw = {
            "cli_pool": {
                "idle_ttl": 600,
                "default_timeout": 120,
                "turn_timeout": 300,
            }
        }
        result = _load_cli_pool_config(raw)
        assert result.idle_ttl == 600
        assert result.default_timeout == 120
        assert result.turn_timeout == 300

    def test_partial_override_keeps_defaults(self) -> None:
        """SC-1: Only turn_timeout set → idle_ttl and default_timeout keep defaults."""
        from lyra.bootstrap.factory.config import _load_cli_pool_config

        raw = {"cli_pool": {"turn_timeout": 600}}
        result = _load_cli_pool_config(raw)
        assert result.idle_ttl == 1200
        assert result.default_timeout == 1200
        assert result.turn_timeout == 600


# ---------------------------------------------------------------------------
# _load_pool_config (#369)
# ---------------------------------------------------------------------------


class TestLoadPoolConfig:
    """#369: _load_pool_config reads [pool] section with defaults."""

    def test_defaults_when_section_missing(self) -> None:
        """Missing [pool] → hardcoded defaults returned."""
        from lyra.bootstrap.factory.config import _load_pool_config

        result = _load_pool_config({})
        assert result.safe_dispatch_timeout == 10.0

    def test_overrides_from_toml(self) -> None:
        """TOML values override defaults."""
        from lyra.bootstrap.factory.config import _load_pool_config

        raw = {"pool": {"safe_dispatch_timeout": 30.0}}
        result = _load_pool_config(raw)
        assert result.safe_dispatch_timeout == 30.0


# ---------------------------------------------------------------------------
# _load_llm_config (#369)
# ---------------------------------------------------------------------------


class TestLoadLlmConfig:
    """#369: _load_llm_config reads [llm] section with defaults."""

    def test_defaults_when_section_missing(self) -> None:
        """Missing [llm] → hardcoded defaults returned."""
        from lyra.bootstrap.factory.config import _load_llm_config

        result = _load_llm_config({})
        assert result.max_retries == 3
        assert result.backoff_base == 1.0

    def test_overrides_from_toml(self) -> None:
        """TOML values override defaults."""
        from lyra.bootstrap.factory.config import _load_llm_config

        raw = {"llm": {"max_retries": 5, "backoff_base": 2.0}}
        result = _load_llm_config(raw)
        assert result.max_retries == 5
        assert result.backoff_base == 2.0

    def test_partial_override_keeps_defaults(self) -> None:
        """Only max_retries set → backoff_base keeps default."""
        from lyra.bootstrap.factory.config import _load_llm_config

        raw = {"llm": {"max_retries": 10}}
        result = _load_llm_config(raw)
        assert result.max_retries == 10
        assert result.backoff_base == 1.0


# ---------------------------------------------------------------------------
# _load_inbound_bus_config (#369)
# ---------------------------------------------------------------------------


class TestLoadInboundBusConfig:
    """#369: _load_inbound_bus_config reads [inbound_bus] section with defaults."""

    def test_defaults_when_section_missing(self) -> None:
        """Missing [inbound_bus] → hardcoded defaults returned."""
        from lyra.bootstrap.factory.config import _load_inbound_bus_config

        result = _load_inbound_bus_config({})
        assert result.queue_depth_threshold == 100
        assert result.staging_maxsize == 500
        assert result.platform_queue_maxsize == 100

    def test_overrides_from_toml(self) -> None:
        """TOML values override defaults."""
        from lyra.bootstrap.factory.config import _load_inbound_bus_config

        raw = {
            "inbound_bus": {
                "queue_depth_threshold": 200,
                "staging_maxsize": 1000,
                "platform_queue_maxsize": 50,
            }
        }
        result = _load_inbound_bus_config(raw)
        assert result.queue_depth_threshold == 200
        assert result.staging_maxsize == 1000
        assert result.platform_queue_maxsize == 50

    def test_partial_override_keeps_defaults(self) -> None:
        """Only staging_maxsize set → other keys keep defaults."""
        from lyra.bootstrap.factory.config import _load_inbound_bus_config

        raw = {"inbound_bus": {"staging_maxsize": 2000}}
        result = _load_inbound_bus_config(raw)
        assert result.staging_maxsize == 2000
        assert result.queue_depth_threshold == 100
        assert result.platform_queue_maxsize == 100


# ---------------------------------------------------------------------------
# _load_debouncer_config (#369)
# ---------------------------------------------------------------------------


class TestLoadDebouncerConfig:
    """#369: _load_debouncer_config reads [debouncer] section with defaults."""

    def test_defaults_when_section_missing(self) -> None:
        """Missing [debouncer] → hardcoded defaults returned."""
        from lyra.bootstrap.factory.config import _load_debouncer_config

        result = _load_debouncer_config({})
        assert result.default_debounce_ms == 300
        assert result.max_merged_chars == 4096

    def test_overrides_from_toml(self) -> None:
        """TOML values override defaults."""
        from lyra.bootstrap.factory.config import _load_debouncer_config

        raw = {"debouncer": {"default_debounce_ms": 500, "max_merged_chars": 8192}}
        result = _load_debouncer_config(raw)
        assert result.default_debounce_ms == 500
        assert result.max_merged_chars == 8192

    def test_partial_override_keeps_defaults(self) -> None:
        """Only max_merged_chars set → default_debounce_ms keeps default."""
        from lyra.bootstrap.factory.config import _load_debouncer_config

        raw = {"debouncer": {"max_merged_chars": 1024}}
        result = _load_debouncer_config(raw)
        assert result.max_merged_chars == 1024
        assert result.default_debounce_ms == 300


# ---------------------------------------------------------------------------
# _build_agent_overrides (#411)
# ---------------------------------------------------------------------------


class TestBuildAgentOverrides:
    def test_defaults_only(self) -> None:
        from lyra.bootstrap.factory.config import _build_agent_overrides

        raw = {"defaults": {"cwd": "/tmp"}}
        result = _build_agent_overrides(raw, "test")
        assert result.cwd == "/tmp"

    def test_agent_specific_wins(self) -> None:
        from lyra.bootstrap.factory.config import _build_agent_overrides

        raw = {
            "defaults": {"cwd": "/default"},
            "agents": {"myagent": {"cwd": "/specific"}},
        }
        result = _build_agent_overrides(raw, "myagent")
        assert result.cwd == "/specific"

    def test_workspace_deep_merge(self) -> None:
        from lyra.bootstrap.factory.config import _build_agent_overrides

        raw = {
            "defaults": {"workspaces": {"a": "/a", "b": "/b"}},
            "agents": {"myagent": {"workspaces": {"b": "/b2", "c": "/c"}}},
        }
        result = _build_agent_overrides(raw, "myagent")
        assert result.workspaces == {"a": "/a", "b": "/b2", "c": "/c"}

    def test_empty_config(self) -> None:
        from lyra.bootstrap.factory.config import _build_agent_overrides

        result = _build_agent_overrides({}, "nonexistent")
        assert result.cwd is None
        assert result.workspaces == {}
