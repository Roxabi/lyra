"""Tests for Layer 1 monitoring checks (issue #111, SC-4, SC-6, SC-11)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# check_process
# ---------------------------------------------------------------------------


class TestCheckProcess:
    def test_active_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_process passes for active service."""
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="active\n"),
        )
        from lyra.monitoring.checks import check_process

        result = check_process("lyra")
        assert result.passed is True
        assert result.name == "process"

    def test_inactive_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_process fails for inactive service."""
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=3, stdout="inactive\n"),
        )
        from lyra.monitoring.checks import check_process

        result = check_process("lyra")
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_http_health
# ---------------------------------------------------------------------------


class TestCheckHttpHealth:
    async def test_healthy_endpoint(self) -> None:
        """SC-4: check_http_health passes when endpoint returns 200."""
        from lyra.monitoring.checks import check_http_health

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "queue_size": 5,
            "last_message_age_s": 30.0,
            "uptime_s": 3600.0,
            "circuits": {"anthropic": {"state": "closed"}},
        }

        with patch("lyra.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result, data = await check_http_health("http://localhost:8443/health", 5)

        assert result.passed is True
        assert result.name == "http_health"
        assert data is not None
        assert data["queue_size"] == 5

    async def test_unreachable_endpoint(self) -> None:
        """SC-4: check_http_health fails when endpoint is unreachable."""
        import httpx

        from lyra.monitoring.checks import check_http_health

        with patch("lyra.monitoring.checks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result, data = await check_http_health("http://localhost:8443/health", 5)

        assert result.passed is False
        assert data is None


# ---------------------------------------------------------------------------
# check_queue_depth
# ---------------------------------------------------------------------------


class TestCheckQueueDepth:
    def test_below_threshold(self) -> None:
        """SC-4: check_queue_depth passes when queue_size < threshold."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 10}, 80)
        assert result.passed is True
        assert result.name == "queue_depth"

    def test_above_threshold(self) -> None:
        """SC-4: check_queue_depth fails when queue_size >= threshold."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 90}, 80)
        assert result.passed is False

    def test_at_exact_threshold(self) -> None:
        """Boundary: queue_size == threshold should fail (uses strict <)."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 80}, 80)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_idle
# ---------------------------------------------------------------------------


class TestCheckIdle:
    def test_within_threshold(self) -> None:
        """SC-6: check_idle passes when last_message_age_s is within threshold."""
        from lyra.monitoring.checks import check_idle

        result = check_idle(
            {"last_message_age_s": 3600.0},  # 1 hour
            threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
        )
        assert result.passed is True

    def test_exceeds_threshold(self) -> None:
        """SC-6: check_idle fails when last_message_age_s exceeds threshold."""
        from lyra.monitoring.checks import check_idle

        result = check_idle(
            {"last_message_age_s": 25200.0},  # 7 hours
            threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
        )
        assert result.passed is False

    def test_skips_during_quiet_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-6: check_idle passes during quiet hours regardless of age."""
        from lyra.monitoring import checks

        # Mock current time to 03:00
        mock_now = datetime(2026, 3, 10, 3, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            "lyra.monitoring.checks.datetime",
            type(
                "MockDatetime",
                (),
                {
                    "now": staticmethod(lambda tz=None: mock_now),
                    "strptime": datetime.strptime,
                },
            ),
        )

        result = checks.check_idle(
            {"last_message_age_s": 25200.0},  # 7 hours — would fail normally
            threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
        )
        assert result.passed is True
        assert "quiet hours" in result.detail.lower()

    def test_skips_during_midnight_wrap_quiet_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quiet hours wrapping midnight (22:00-06:00) at 23:30."""
        from lyra.monitoring import checks

        mock_now = datetime(2026, 3, 10, 23, 30, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            "lyra.monitoring.checks.datetime",
            type(
                "MockDatetime",
                (),
                {
                    "now": staticmethod(lambda tz=None: mock_now),
                    "strptime": datetime.strptime,
                },
            ),
        )

        result = checks.check_idle(
            {"last_message_age_s": 25200.0},
            threshold_hours=6,
            quiet_start="22:00",
            quiet_end="06:00",
        )
        assert result.passed is True
        assert "quiet hours" in result.detail.lower()

    def test_null_last_message_passes(self) -> None:
        """SC-6: check_idle passes when last_message_age_s is null (no messages yet)."""
        from lyra.monitoring.checks import check_idle

        result = check_idle(
            {"last_message_age_s": None},
            threshold_hours=6,
            quiet_start="00:00",
            quiet_end="08:00",
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# check_circuits
# ---------------------------------------------------------------------------


class TestCheckCircuits:
    def test_all_closed(self) -> None:
        """SC-4: check_circuits passes when all circuits are closed."""
        from lyra.monitoring.checks import check_circuits

        health_json = {
            "circuits": {
                "anthropic": {"state": "closed"},
                "telegram": {"state": "closed"},
            }
        }
        result = check_circuits(health_json)
        assert result.passed is True
        assert result.name == "circuits"

    def test_open_circuit(self) -> None:
        """SC-4: check_circuits fails when any circuit is open."""
        from lyra.monitoring.checks import check_circuits

        health_json = {
            "circuits": {
                "anthropic": {"state": "open"},
                "telegram": {"state": "closed"},
            }
        }
        result = check_circuits(health_json)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------


class TestCheckDisk:
    def test_sufficient_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_disk passes when free space is above threshold."""
        import shutil

        monkeypatch.setattr(
            "lyra.monitoring.checks.shutil.disk_usage",
            lambda path: shutil._ntuple_diskusage(
                total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
            ),
        )
        from lyra.monitoring.checks import check_disk

        result = check_disk("/", 1)
        assert result.passed is True
        assert result.name == "disk"

    def test_insufficient_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_disk fails when free space is below threshold."""
        import shutil

        monkeypatch.setattr(
            "lyra.monitoring.checks.shutil.disk_usage",
            lambda path: shutil._ntuple_diskusage(
                total=100 * 1024**3,
                used=int(99.5 * 1024**3),
                free=int(0.5 * 1024**3),
            ),
        )
        from lyra.monitoring.checks import check_disk

        result = check_disk("/", 1)
        assert result.passed is False


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

        # Mock all external calls
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="active\n"),
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
        # process + http_health + queue_depth + circuits + disk = 5 (idle disabled)
        assert len(report.checks) == 5

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

        # Process check fails
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=3, stdout="inactive\n"),
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
