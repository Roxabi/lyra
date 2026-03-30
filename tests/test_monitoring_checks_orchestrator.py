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
        from lyra.monitoring.checks import run_checks
        from lyra.monitoring.config import MonitoringConfig

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
            anthropic_api_key="fake",
            telegram_admin_chat_id="12345",
            disk_check_path="/",
            service_name="lyra",
        )

        # Mock all external calls (supervisor-style output for process check)
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=0,
                stdout="lyra                             RUNNING   pid 1234, uptime 1:00:00\n",
            ),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "queue_size": 5,
            "last_message_age_s": 30.0,
            "uptime_s": 3600.0,
            "circuits": {
                "anthropic": {"state": "closed"},
                "telegram": {"state": "closed"},
                "discord": {"state": "closed"},
                "hub": {"state": "closed"},
            },
            "reaper_alive": True,
            "reaper_last_sweep_age": 30.0,
        }

        with patch("lyra.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            import shutil

            monkeypatch.setattr(
                "lyra.monitoring.checks.shutil.disk_usage",
                lambda path: shutil._ntuple_diskusage(
                    total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
                ),
            )

            report = await run_checks(config)

        assert report.all_passed is True
        assert report.failed_count == 0
        # process + http_health + queue_depth + circuits + reaper + disk = 6
        assert len(report.checks) == 6

    async def test_failure_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-11: run_checks returns all_passed=False when a check fails."""
        from lyra.monitoring.checks import run_checks
        from lyra.monitoring.config import MonitoringConfig

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
            anthropic_api_key="fake",
            telegram_admin_chat_id="12345",
            disk_check_path="/",
            service_name="lyra",
        )

        # Process check fails (supervisor-style output)
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=3,
                stdout="lyra                             STOPPED   Mar 30 12:00 PM\n",
            ),
        )

        # HTTP also fails (hub is down)
        import httpx

        with patch("lyra.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            import shutil

            monkeypatch.setattr(
                "lyra.monitoring.checks.shutil.disk_usage",
                lambda path: shutil._ntuple_diskusage(
                    total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
                ),
            )

            report = await run_checks(config)

        assert report.all_passed is False
        assert report.failed_count >= 1
