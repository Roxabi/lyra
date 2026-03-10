"""Tests for Layer 2 LLM escalation + Telegram notification (issue #111, SC-7–SC-10)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.monitoring.config import MonitoringConfig
from lyra.monitoring.models import CheckResult, DiagnosisReport, HealthReport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config() -> MonitoringConfig:
    return MonitoringConfig(
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
        telegram_token="tg-token",
        anthropic_api_key="sk-ant-key",
        telegram_admin_chat_id="12345",
        disk_check_path="/",
        service_name="lyra",
    )


@pytest.fixture()
def failed_report() -> HealthReport:
    now = datetime.now(timezone.utc)
    return HealthReport(
        checks=[
            CheckResult(name="process", passed=False, detail="inactive", timestamp=now),
            CheckResult(name="disk", passed=True, detail="50GB free", timestamp=now),
        ],
        all_passed=False,
        failed_count=1,
        timestamp=now,
    )


@pytest.fixture()
def diagnosis() -> DiagnosisReport:
    return DiagnosisReport(
        severity="critical",
        diagnosis="The lyra systemd service has stopped.",
        suggested_remediation="Run: sudo systemctl restart lyra",
        source=HealthReport(
            checks=[],
            all_passed=False,
            failed_count=1,
            timestamp=datetime.now(timezone.utc),
        ),
    )


# ---------------------------------------------------------------------------
# escalate_to_llm
# ---------------------------------------------------------------------------


class TestEscalateToLLM:
    async def test_calls_anthropic(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """SC-7: escalate_to_llm calls Anthropic API and returns DiagnosisReport."""
        from lyra.monitoring.escalation import escalate_to_llm

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"severity":"warning","diagnosis":"Process down",'
                    '"suggested_remediation":"Restart service"}',
                }
            ]
        }

        with patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await escalate_to_llm(failed_report, config)

        assert isinstance(result, DiagnosisReport)
        assert result.severity == "warning"
        assert result.diagnosis == "Process down"

    async def test_raises_on_api_error(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """SC-9: escalate_to_llm raises on Anthropic API failure."""
        import httpx

        from lyra.monitoring.escalation import escalate_to_llm

        with patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("API unreachable")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(Exception):
                await escalate_to_llm(failed_report, config)


# ---------------------------------------------------------------------------
# send_telegram_alert
# ---------------------------------------------------------------------------


class TestSendTelegramAlert:
    async def test_sends_message(
        self, config: MonitoringConfig, diagnosis: DiagnosisReport
    ) -> None:
        """SC-8: send_telegram_alert sends formatted message to admin chat."""
        from lyra.monitoring.escalation import send_telegram_alert

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await send_telegram_alert(diagnosis, config)

            # Verify the call was made to Telegram Bot API
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "12345" in str(call_args)  # chat_id in request


# ---------------------------------------------------------------------------
# send_telegram_raw_alert
# ---------------------------------------------------------------------------


class TestSendTelegramRawAlert:
    async def test_sends_raw_checks(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """SC-9: send_telegram_raw_alert sends raw check results."""
        from lyra.monitoring.escalation import send_telegram_raw_alert

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await send_telegram_raw_alert(failed_report, config)

            mock_client.post.assert_called_once()

    async def test_raises_on_telegram_failure(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """SC-10: send_telegram_raw_alert raises when Telegram is unreachable."""
        import httpx

        from lyra.monitoring.escalation import send_telegram_raw_alert

        with patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Telegram unreachable")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(Exception):
                await send_telegram_raw_alert(failed_report, config)
