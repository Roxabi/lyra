"""Tests for the run_checks orchestrator (issue #111, SC-4, SC-11)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# run_checks orchestrator
# ---------------------------------------------------------------------------


class TestRunChecks:
    async def test_all_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4, SC-11: run_checks returns all_passed=True when all checks pass."""
        from factory.monitoring.checks import run_checks
        from factory.monitoring.config import MonitoringConfig

        config = MonitoringConfig(
            check_interval_minutes=5,
            health_endpoint_timeout_s=5,
            queue_depth_threshold=80,
            idle_threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
            idle_check_enabled=False,
            min_disk_free_gb=1,
            health_endpoint_url="http://localhost:8443/health",
            diagnostic_model="claude-haiku-4-5-20251001",
            telegram_token="fake",
            telegram_admin_chat_id="12345",
            disk_check_path="/",
            service_names=["lyra-hub"],
        )

        # Mock systemctl --user is-active
        monkeypatch.setattr(
            "factory.monitoring.checks.subprocess.run",
            MagicMock(return_value=MagicMock(returncode=0, stdout="active\n")),
        )

        # Mock podman logs for log-scan checks (empty output → 0 matches → passed)
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "queue_size": 5,
            "last_message_age_s": 30.0,
            "uptime_s": 3600.0,
            "circuits": {
                "claude-cli": {"state": "closed"},
                "telegram": {"state": "closed"},
                "discord": {"state": "closed"},
                "hub": {"state": "closed"},
            },
            "reaper_alive": True,
            "reaper_last_sweep_age": 30.0,
        }

        varz_response = MagicMock()
        varz_response.status_code = 200
        varz_response.json.return_value = {"auth_errors": 0, "slow_consumers": 0}

        # /jsz → 404: stream not yet provisioned → audio checks pass/skip (#1482 T11)
        jsz_response = MagicMock()
        jsz_response.status_code = 404

        # Single patch covers all monitoring modules: checks.py, checks_varz.py, and
        # checks_audio.py all reference the same httpx module object, so patching
        # httpx.AsyncClient via any one of those namespaces patches it globally.
        with patch("factory.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            def _mock_get(url: str, **_kwargs: object) -> MagicMock:  # type: ignore
                if "/varz" in url:
                    return varz_response
                if "/jsz" in url:
                    return jsz_response
                return mock_response

            mock_client.get.side_effect = _mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            import shutil

            monkeypatch.setattr(
                "factory.monitoring.checks_varz.shutil.disk_usage",
                lambda _: shutil._ntuple_diskusage(
                    total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
                ),
            )

            import os as _os

            monkeypatch.setattr(
                "factory.monitoring.checks_varz.os.statvfs",
                lambda _: _os.statvfs_result(
                    (
                        100 * 1024**3,
                        50 * 1024**3,
                        50 * 1024**3,
                        1000000,
                        900000,
                        1000,
                        700,
                        700,
                        0,
                        0,
                    )
                ),
            )

            report = await run_checks(config)

        assert report.all_passed is True
        assert report.failed_count == 0
        assert {c.name for c in report.checks} == {
            "process:lyra-hub",
            "http_health",
            "queue_depth",
            "circuits",
            "reaper",
            "nats:permissions_violation",
            "hub:dict_stream_gen_timeout",
            "disk",
            "nats:varz",
            "disk_pct",
            "inode_pct",
            "audio:consumer_lag",
            "audio:stream_usage",
        }

    async def test_failure_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-11: run_checks returns all_passed=False when a check fails."""
        from factory.monitoring.checks import run_checks
        from factory.monitoring.config import MonitoringConfig

        config = MonitoringConfig(
            check_interval_minutes=5,
            health_endpoint_timeout_s=5,
            queue_depth_threshold=80,
            idle_threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
            idle_check_enabled=False,
            min_disk_free_gb=1,
            health_endpoint_url="http://localhost:8443/health",
            diagnostic_model="claude-haiku-4-5-20251001",
            telegram_token="fake",
            telegram_admin_chat_id="12345",
            disk_check_path="/",
            service_names=["lyra-hub"],
        )

        # Process check fails — systemctl returns inactive
        monkeypatch.setattr(
            "factory.monitoring.checks.subprocess.run",
            MagicMock(return_value=MagicMock(returncode=3, stdout="inactive\n")),
        )

        # Mock podman logs for log-scan checks
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
        )

        # HTTP also fails (hub is down); audio checks use the same mock client but
        # /jsz → 404 so they pass/skip and do NOT add to failed_count (#1482 T11).
        import httpx

        jsz_response = MagicMock()
        jsz_response.status_code = 404

        with patch("factory.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            def _mock_get_failure(url: str, **_kwargs: object) -> MagicMock:  # type: ignore
                if "/jsz" in url:
                    return jsz_response
                raise httpx.ConnectError("Connection refused")

            mock_client.get.side_effect = _mock_get_failure
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            import shutil

            monkeypatch.setattr(
                "factory.monitoring.checks_varz.shutil.disk_usage",
                lambda _: shutil._ntuple_diskusage(
                    total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
                ),
            )

            import os as _os

            monkeypatch.setattr(
                "factory.monitoring.checks_varz.os.statvfs",
                lambda _: _os.statvfs_result(
                    (
                        100 * 1024**3,
                        50 * 1024**3,
                        50 * 1024**3,
                        1000000,
                        900000,
                        1000,
                        700,
                        700,
                        0,
                        0,
                    )
                ),
            )

            report = await run_checks(config)

        assert report.all_passed is False
        assert report.failed_count == 2
        assert {c.name for c in report.checks} == {
            "process:lyra-hub",
            "http_health",
            "nats:permissions_violation",
            "hub:dict_stream_gen_timeout",
            "disk",
            "nats:varz",
            "disk_pct",
            "inode_pct",
            "audio:consumer_lag",
            "audio:stream_usage",
        }
