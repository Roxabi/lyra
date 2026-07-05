"""LogMonitorLoop — V1-pull periodic scheduler for the two log-scanning checks (#2245).

No NATS connection, no event/metric bus producer — ADR-091 plane③ V1: pull, not V2:
subscribe (#1035, future work). This is a thin scheduler around the existing pure
check functions in checks_log.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import escalation
from ._errors import _MONITORING_ESCALATION_ERRORS
from .checks_log import (
    LogFetcher,
    LokiLogFetcher,
    check_hub_dict_stream_gen_timeout,
    check_nats_log_errors,
)
from .config import MonitoringConfig
from .models import CheckResult, HealthReport

log = logging.getLogger(__name__)


class LogMonitorLoop:
    """Periodically runs the NATS-permissions and hub-stream-gen log checks."""

    def __init__(
        self,
        config: MonitoringConfig,
        fetcher: LogFetcher = LokiLogFetcher(),
    ) -> None:
        # Default is Loki (ADR-093), not SubprocessLogFetcher: the deployed
        # factory-log-monitor container has no Podman socket/CLI (plan SC2) —
        # a subprocess default would silently fail every check in prod.
        self.config = config
        self.fetcher = fetcher

    async def run_once(self) -> HealthReport:
        """Run both log checks once and return an aggregated HealthReport."""
        checks: list[CheckResult] = [
            await asyncio.to_thread(
                check_nats_log_errors,
                self.config.nats_container_name,
                self.config.nats_log_check_minutes,
                fetcher=self.fetcher,
            ),
            await asyncio.to_thread(
                check_hub_dict_stream_gen_timeout,
                self.config.hub_container_name,
                self.config.stream_gen_timeout_minutes,
                self.config.stream_gen_timeout_threshold,
                fetcher=self.fetcher,
            ),
        ]
        failed = [c for c in checks if not c.passed]
        return HealthReport(
            checks=checks,
            all_passed=len(failed) == 0,
            failed_count=len(failed),
            timestamp=datetime.now(timezone.utc),
        )

    async def run_forever(self) -> None:
        """Loop run_once() on config.check_interval_minutes, alerting on any failure."""
        while True:
            report = await self.run_once()
            if not report.all_passed:
                log.warning(
                    "log-monitor: %d/%d checks failed",
                    report.failed_count,
                    len(report.checks),
                )
                try:
                    await escalation.send_telegram_raw_alert(report, self.config)
                except _MONITORING_ESCALATION_ERRORS:
                    log.exception("log-monitor: Telegram alert delivery failed")
            else:
                log.info("log-monitor: all checks passed")
            await asyncio.sleep(self.config.check_interval_minutes * 60)
