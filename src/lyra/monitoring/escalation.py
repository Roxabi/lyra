"""Layer 2: LLM diagnosis + Telegram notification on anomaly."""

from __future__ import annotations

import json
import logging

import httpx

from .config import MonitoringConfig
from .models import DiagnosisReport, HealthReport

log = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4000  # Telegram limit is 4096, leave margin

_DIAGNOSTIC_SYSTEM_PROMPT = """\
You are a system health diagnostic assistant for the Lyra AI agent hub.
You will receive a health check report with failed checks.
Respond with a JSON object containing exactly these fields:
- "severity": one of "info", "warning", "critical"
- "diagnosis": a concise explanation of what is wrong (1-2 sentences)
- "suggested_remediation": specific actionable steps to fix the issue (1-2 sentences)

Respond ONLY with the JSON object, no other text."""


async def escalate_to_llm(
    report: HealthReport, config: MonitoringConfig
) -> DiagnosisReport:
    """Call Anthropic API with health report for LLM diagnosis.

    Raises on any API or parsing failure — caller handles fallback.
    """
    failed_checks = [
        {"name": c.name, "detail": c.detail} for c in report.checks if not c.passed
    ]
    passed_checks = [
        {"name": c.name, "detail": c.detail} for c in report.checks if c.passed
    ]

    user_message = (
        f"Health check report at {report.timestamp.isoformat()}:\n"
        f"Failed checks ({report.failed_count}):\n"
        + "\n".join(f"  - {c['name']}: {c['detail']}" for c in failed_checks)
        + "\nPassing checks:\n"
        + "\n".join(f"  - {c['name']}: {c['detail']}" for c in passed_checks)
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.diagnostic_model,
                "max_tokens": 256,
                "system": _DIAGNOSTIC_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=30,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API returned {resp.status_code}: {resp.text}")

    data = resp.json()
    text = data["content"][0]["text"]
    parsed = json.loads(text)

    _VALID_SEVERITIES = {"info", "warning", "critical"}
    severity = parsed.get("severity", "warning")
    if severity not in _VALID_SEVERITIES:
        log.warning(
            "LLM returned unexpected severity %r, defaulting to 'warning'",
            severity,
        )
        severity = "warning"

    return DiagnosisReport(
        severity=severity,
        diagnosis=parsed["diagnosis"],
        suggested_remediation=parsed["suggested_remediation"],
        source=report,
    )


def _format_diagnosis_message(diagnosis: DiagnosisReport) -> str:
    """Format a Telegram alert message from a DiagnosisReport."""
    severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
        diagnosis.severity, "❓"
    )

    failed = [c for c in diagnosis.source.checks if not c.passed]
    check_lines = "\n".join(f"  ❌ {c.name}: {c.detail}" for c in failed)

    return (
        f"{severity_emoji} Lyra Health Alert [{diagnosis.severity.upper()}]\n\n"
        f"Failed checks:\n{check_lines}\n\n"
        f"Diagnosis: {diagnosis.diagnosis}\n\n"
        f"Remediation: {diagnosis.suggested_remediation}"
    )


def _format_raw_alert(report: HealthReport) -> str:
    """Format a raw Telegram alert from a HealthReport (no LLM diagnosis)."""
    failed = [c for c in report.checks if not c.passed]
    check_lines = "\n".join(f"  ❌ {c.name}: {c.detail}" for c in failed)

    return (
        f"🚨 Lyra Health Alert [RAW — LLM unavailable]\n\n"
        f"Failed checks ({report.failed_count}):\n{check_lines}\n\n"
        f"LLM diagnosis unavailable. Manual investigation required."
    )


async def send_telegram_alert(
    diagnosis: DiagnosisReport, config: MonitoringConfig
) -> None:
    """Send formatted diagnosis to Telegram admin chat.

    Raises on delivery failure — caller handles fallback.
    """
    message = _format_diagnosis_message(diagnosis)
    await _send_telegram_message(message, config)


async def send_telegram_raw_alert(
    report: HealthReport, config: MonitoringConfig
) -> None:
    """Send raw check results to Telegram admin chat.

    Raises on delivery failure — caller handles log fallback.
    """
    message = _format_raw_alert(report)
    await _send_telegram_message(message, config)


async def _send_telegram_message(text: str, config: MonitoringConfig) -> None:
    """Send a message via Telegram Bot API (direct httpx, not through hub)."""
    if len(text) > TELEGRAM_MAX_LEN:
        text = text[:TELEGRAM_MAX_LEN] + "\n…[truncated]"

    url = f"https://api.telegram.org/bot{config.telegram_token}/sendMessage"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={
                # No parse_mode — plain text prevents HTML injection from LLM output
                "chat_id": config.telegram_admin_chat_id,
                "text": text,
            },
            timeout=10,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram API returned {resp.status_code}: {resp.text}")
