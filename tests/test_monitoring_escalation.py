"""Tests for Layer 2 LLM escalation + Telegram notification (issue #111, SC-7–SC-10)."""

from __future__ import annotations

import shutil
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
    async def test_prefers_cli_over_api(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """escalate_to_llm uses Claude CLI when available."""
        from lyra.monitoring.escalation import escalate_to_llm

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            b'{"severity":"critical","diagnosis":"Service crashed",'
            b'"suggested_remediation":"Restart it"}',
            b"",
        )

        with (
            patch(
                "lyra.monitoring.escalation.shutil.which",
                return_value="/usr/bin/claude",
            ),
            patch(
                "lyra.monitoring.escalation.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "lyra.monitoring.escalation.asyncio.wait_for",
                return_value=mock_proc.communicate.return_value,
            ),
        ):
            result = await escalate_to_llm(failed_report, config)

        assert isinstance(result, DiagnosisReport)
        assert result.severity == "critical"
        assert result.diagnosis == "Service crashed"

    async def test_raises_on_api_error(
        self, config: MonitoringConfig, failed_report: HealthReport
    ) -> None:
        """SC-9: escalate_to_llm raises on Anthropic API failure."""
        import httpx

        from lyra.monitoring.escalation import escalate_to_llm

        with (
            patch("lyra.monitoring.escalation.shutil.which", return_value=None),
            patch("lyra.monitoring.escalation.httpx.AsyncClient") as mock_client_cls,
        ):
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

    async def test_no_parse_mode_in_telegram_payload(
        self, config: MonitoringConfig, diagnosis: DiagnosisReport
    ) -> None:
        """Telegram payload must not contain parse_mode (HTML injection)."""
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

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args.kwargs
            posted_json = call_kwargs.get("json", {})
            assert "parse_mode" not in posted_json


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


# ---------------------------------------------------------------------------
# _run() fallback chain (monitoring.__main__)
# ---------------------------------------------------------------------------


class TestRunFallbackChain:
    """Tests for the monitoring pipeline orchestration in _run()."""

    @pytest.fixture()
    def _mock_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        """Set env vars so load_monitoring_config() succeeds."""
        monkeypatch.setenv("LYRA_CONFIG", str(tmp_path / "nonexistent.toml"))
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "12345")
        # Mock disk usage for check_disk
        monkeypatch.setattr(
            "lyra.monitoring.checks.shutil.disk_usage",
            lambda path: shutil._ntuple_diskusage(
                total=100 * 1024**3,
                used=50 * 1024**3,
                free=50 * 1024**3,
            ),
        )

    async def test_all_pass_returns_zero(
        self,
        _mock_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All checks pass → exit 0, no LLM call."""
        from lyra.monitoring.__main__ import _run

        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=0,
                stdout="lyra_telegram                    RUNNING   pid 1234, uptime 1:00:00\n",  # noqa: E501
            ),
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "queue_size": 0,
            "last_message_age_s": 10.0,
            "uptime_s": 100.0,
            "circuits": {"anthropic": {"state": "closed"}},
        }

        with patch("lyra.monitoring.checks.httpx.AsyncClient") as mock_cls:
            mc = AsyncMock()
            mc.get.return_value = mock_resp
            mc.__aenter__ = AsyncMock(return_value=mc)
            mc.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mc

            code = await _run()

        assert code == 0

    async def test_llm_fail_raw_telegram_sent(
        self,
        _mock_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Anomaly + CLI unavailable → raw Telegram sent → exit 1.

        After SDK removal escalate_to_llm raises immediately when claude CLI is
        not installed (no HTTP fallback to Anthropic API).  The _run() fallback
        chain catches the RuntimeError and calls send_telegram_raw_alert.
        """
        from lyra.monitoring.__main__ import _run

        # Process check fails → anomaly
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=3, stdout="inactive\n"),
        )

        import httpx

        call_log: list[str] = []

        with (
            # CLI not installed → escalate_to_llm raises RuntimeError immediately
            patch("lyra.monitoring.escalation.shutil.which", return_value=None),
            patch("lyra.monitoring.checks.httpx.AsyncClient") as checks_cls,
            patch("lyra.monitoring.escalation.httpx.AsyncClient") as esc_cls,
        ):
            # HTTP health check fails (hub down)
            mc_checks = AsyncMock()
            mc_checks.get.side_effect = httpx.ConnectError("refused")
            mc_checks.__aenter__ = AsyncMock(return_value=mc_checks)
            mc_checks.__aexit__ = AsyncMock(return_value=False)
            checks_cls.return_value = mc_checks

            # Telegram raw alert succeeds
            async def mock_post(*args, **kwargs):
                call_log.append("telegram_raw")
                resp = MagicMock()
                resp.status_code = 200
                return resp

            mc_esc = AsyncMock()
            mc_esc.post = mock_post
            mc_esc.__aenter__ = AsyncMock(return_value=mc_esc)
            mc_esc.__aexit__ = AsyncMock(return_value=False)
            esc_cls.return_value = mc_esc

            code = await _run()

        assert code == 1
        assert "telegram_raw" in call_log

    async def test_both_fail_log_only(
        self,
        _mock_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Anomaly + LLM fails + Telegram fails → log only → exit 1."""
        from lyra.monitoring.__main__ import _run

        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=3, stdout="inactive\n"),
        )

        import httpx

        with (
            patch("lyra.monitoring.escalation.shutil.which", return_value=None),
            patch("lyra.monitoring.checks.httpx.AsyncClient") as checks_cls,
            patch("lyra.monitoring.escalation.httpx.AsyncClient") as esc_cls,
        ):
            mc_checks = AsyncMock()
            mc_checks.get.side_effect = httpx.ConnectError("refused")
            mc_checks.__aenter__ = AsyncMock(return_value=mc_checks)
            mc_checks.__aexit__ = AsyncMock(return_value=False)
            checks_cls.return_value = mc_checks

            # Both LLM and Telegram fail
            mc_esc = AsyncMock()
            mc_esc.post.side_effect = httpx.ConnectError("all down")
            mc_esc.__aenter__ = AsyncMock(return_value=mc_esc)
            mc_esc.__aexit__ = AsyncMock(return_value=False)
            esc_cls.return_value = mc_esc

            code = await _run()

        assert code == 1
