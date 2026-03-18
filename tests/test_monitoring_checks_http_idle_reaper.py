"""Tests for Layer 1 monitoring checks: http_health, idle/quiet-hours, reaper."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

    def test_exceeds_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-6: check_idle fails when last_message_age_s exceeds threshold."""
        from lyra.monitoring import checks

        # Pin time to midday so quiet window (00:00-08:00) never suppresses.
        mock_now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        with patch("lyra.monitoring.checks.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.strptime = datetime.strptime
            result = checks.check_idle(
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
        with patch("lyra.monitoring.checks.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.strptime = datetime.strptime
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
        with patch("lyra.monitoring.checks.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.strptime = datetime.strptime
            result = checks.check_idle(
                {"last_message_age_s": 25200.0},
                threshold_hours=6,
                quiet_start="22:00",
                quiet_end="06:00",
            )
        assert result.passed is True
        assert "quiet hours" in result.detail.lower()

    def test_not_quiet_outside_midnight_wrap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """12:00 is NOT in quiet hours for wrap 22:00-06:00."""
        from lyra.monitoring import checks

        mock_now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        with patch("lyra.monitoring.checks.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.strptime = datetime.strptime
            result = checks.check_idle(
                {"last_message_age_s": 25200.0},  # 7 hours — exceeds threshold
                threshold_hours=6,
                quiet_start="22:00",
                quiet_end="06:00",
            )
        assert result.passed is False

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
# check_reaper (#317)
# ---------------------------------------------------------------------------


class TestCheckReaper:
    """#317 SC-11, SC-12: check_reaper validates reaper health from /health JSON."""

    def test_healthy_reaper(self) -> None:
        """SC-12: Reaper alive with recent sweep -> passes."""
        from lyra.monitoring.checks import check_reaper

        result = check_reaper({"reaper_alive": True, "reaper_last_sweep_age": 60.0})
        assert result.passed is True
        assert result.name == "reaper"

    def test_stale_reaper(self) -> None:
        """SC-12: Sweep age > 120s -> fails."""
        from lyra.monitoring.checks import check_reaper

        result = check_reaper({"reaper_alive": True, "reaper_last_sweep_age": 180.0})
        assert result.passed is False
        assert "180" in result.detail

    def test_dead_reaper(self) -> None:
        """SC-11: Reaper not running -> fails."""
        from lyra.monitoring.checks import check_reaper

        result = check_reaper({"reaper_alive": False, "reaper_last_sweep_age": None})
        assert result.passed is False
        assert "not running" in result.detail.lower()

    def test_alive_no_sweep_yet(self) -> None:
        """SC-11: Reaper alive but no sweep yet (before first iteration) -> passes."""
        from lyra.monitoring.checks import check_reaper

        result = check_reaper({"reaper_alive": True, "reaper_last_sweep_age": None})
        assert result.passed is True

    def test_at_threshold_boundary(self) -> None:
        """Boundary: exactly 120s -> passes (uses <=)."""
        from lyra.monitoring.checks import check_reaper

        result = check_reaper({"reaper_alive": True, "reaper_last_sweep_age": 120.0})
        assert result.passed is True
