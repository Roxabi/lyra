"""Tests for monitoring config loading (issue #111, SC-5, SC-6)."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestMonitoringConfigDefaults:
    """Default config when no TOML file exists."""

    def test_load_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-5: Default thresholds applied when no config file."""
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "12345")

        from lyra.monitoring.config import load_monitoring_config

        config = load_monitoring_config()

        assert config.check_interval_minutes == 5
        assert config.health_endpoint_timeout_s == 5
        assert config.queue_depth_threshold == 80
        assert config.idle_threshold_hours == 6
        assert config.min_disk_free_gb == 1
        assert config.idle_check_enabled is False
        assert config.diagnostic_model == "claude-haiku-4-5-20251001"

    def test_secrets_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-5: Secrets come from env vars, not TOML."""
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))
        monkeypatch.setenv("TELEGRAM_TOKEN", "tg-token-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key-456")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "99999")

        from lyra.monitoring.config import load_monitoring_config

        config = load_monitoring_config()

        assert config.telegram_token == "tg-token-123"
        assert config.anthropic_api_key == "sk-ant-key-456"
        assert config.telegram_admin_chat_id == "99999"


class TestMonitoringConfigToml:
    """TOML overrides for thresholds."""

    def test_load_from_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-5: TOML values override defaults."""
        config_file = tmp_path / "lyra.toml"
        config_file.write_text(
            "[monitoring]\n"
            "check_interval_minutes = 10\n"
            "queue_depth_threshold = 50\n"
            "min_disk_free_gb = 5\n"
            'health_endpoint_url = "http://localhost:9000/health"\n'
        )
        monkeypatch.setenv("LYRA_CONFIG", str(config_file))
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "12345")

        from lyra.monitoring.config import load_monitoring_config

        config = load_monitoring_config()

        assert config.check_interval_minutes == 10
        assert config.queue_depth_threshold == 50
        assert config.min_disk_free_gb == 5
        assert config.health_endpoint_url == "http://localhost:9000/health"
        # Non-overridden values keep defaults
        assert config.health_endpoint_timeout_s == 5
        assert config.idle_threshold_hours == 6

    def test_idle_check_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-6: Idle check is opt-in with quiet hours."""
        config_file = tmp_path / "lyra.toml"
        config_file.write_text(
            "[monitoring]\n"
            "idle_check_enabled = true\n"
            'quiet_start = "00:00"\n'
            'quiet_end = "08:00"\n'
        )
        monkeypatch.setenv("LYRA_CONFIG", str(config_file))
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "12345")

        from lyra.monitoring.config import load_monitoring_config

        config = load_monitoring_config()

        assert config.idle_check_enabled is True
        assert config.quiet_start == "00:00"
        assert config.quiet_end == "08:00"

    def test_missing_secrets_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-5: Missing env var secrets raise clear error."""
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)

        from lyra.monitoring.config import load_monitoring_config

        with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
            load_monitoring_config()
