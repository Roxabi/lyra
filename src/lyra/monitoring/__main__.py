"""CLI entrypoint: python -m lyra.monitoring

Runs Layer 1 health checks. On anomaly, escalates to Layer 2 (LLM + Telegram).
Exit 0 = all green, exit 1 = anomaly detected.

NOTE (issue #44): State changes are now logged directly at call sites.
This cron process provides a safety net for catching persistent anomalies.
"""

from __future__ import annotations

import asyncio
import logging

from lyra.core.logging_setup import setup_logging

from .checks import run_checks
from .config import load_monitoring_config
from .escalation import escalate_to_llm, send_telegram_alert, send_telegram_raw_alert

log = logging.getLogger("lyra.monitoring")


async def _run() -> int:
    """Run monitoring pipeline. Returns exit code (0 = OK, 1 = anomaly)."""
    config = load_monitoring_config()
    report = await run_checks(config)

    if report.all_passed:
        log.info("All %d checks passed", len(report.checks))
        return 0

    log.warning(
        "Anomaly detected: %d/%d checks failed",
        report.failed_count,
        len(report.checks),
    )
    for check in report.checks:
        if not check.passed:
            log.warning("  FAIL %s: %s", check.name, check.detail)

    # Layer 2: LLM diagnosis
    try:
        diagnosis = await escalate_to_llm(report, config)
        log.info(
            "LLM diagnosis: severity=%s, diagnosis=%s",
            diagnosis.severity,
            diagnosis.diagnosis,
        )
    except Exception as exc:  # noqa: BLE001 — POLICY:boundary
        log.error("LLM escalation failed: %s", exc)
        # Fallback: raw Telegram alert
        try:
            await send_telegram_raw_alert(report, config)
            log.info("Raw Telegram alert sent (LLM unavailable)")
        except Exception as tg_exc:  # noqa: BLE001 — POLICY:boundary
            log.error(
                "Telegram delivery also failed: %s. Full report logged above. Exit 1.",
                tg_exc,
            )
        return 1

    # Send Telegram alert with diagnosis
    try:
        await send_telegram_alert(diagnosis, config)
        log.info("Telegram alert sent with diagnosis")
    except Exception as tg_exc:  # noqa: BLE001 — POLICY:boundary
        log.error("Telegram delivery failed: %s", tg_exc)
        # Log-only fallback — full report for investigation
        for check in report.checks:
            log.error(
                "  %s %s: %s",
                "PASS" if check.passed else "FAIL",
                check.name,
                check.detail,
            )
        log.error(
            "ALERT NOT DELIVERED. Severity=%s, Diagnosis=%s, Remediation=%s",
            diagnosis.severity,
            diagnosis.diagnosis,
            diagnosis.suggested_remediation,
        )

    return 1


def main() -> int:
    """Entry point for python -m lyra.monitoring."""
    config = load_monitoring_config()
    setup_logging(config.log_level)
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
