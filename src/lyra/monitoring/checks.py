"""Layer 1 deterministic health checks — zero LLM tokens."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import MonitoringConfig
from .models import CheckResult, HealthReport


def check_process(service_name: str) -> CheckResult:
    """Check if a supervisor-managed process is running.

    Uses supervisorctl via deploy/supervisor. Falls back to systemctl if
    supervisorctl is not available.
    """
    now = datetime.now(timezone.utc)
    override = os.environ.get("LYRA_SUPERVISORCTL_PATH")
    if override:
        sctl = Path(override).expanduser()
    else:
        sctl = (
            Path.home() / "projects" / "lyra" / "deploy" / "supervisor"
            / "supervisorctl.sh"
        )

    if sctl.exists():
        try:
            result = subprocess.run(
                [str(sctl), "status", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            active = "RUNNING" in output
            detail = output.split("\n")[0] if output else "unknown"
            return CheckResult(
                name="process", passed=active, detail=detail, timestamp=now
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return CheckResult(
                name="process", passed=False, detail=str(exc), timestamp=now
            )

    # Fallback: systemctl
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.returncode == 0
        detail = result.stdout.strip() if result.stdout else "unknown"
        return CheckResult(name="process", passed=active, detail=detail, timestamp=now)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return CheckResult(name="process", passed=False, detail=str(exc), timestamp=now)


async def check_http_health(
    url: str, timeout: int, health_secret: str = ""
) -> tuple[CheckResult, dict | None]:
    """Check if the hub /health endpoint responds.

    Returns (CheckResult, parsed JSON or None on failure).
    """
    now = datetime.now(timezone.utc)
    headers: dict[str, str] = {}
    if health_secret:
        headers["Authorization"] = f"Bearer {health_secret}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            return (
                CheckResult(
                    name="http_health", passed=True, detail="200 OK", timestamp=now
                ),
                data,
            )
        return (
            CheckResult(
                name="http_health",
                passed=False,
                detail=f"HTTP {resp.status_code}",
                timestamp=now,
            ),
            None,
        )
    except Exception as exc:
        return (
            CheckResult(
                name="http_health", passed=False, detail=str(exc), timestamp=now
            ),
            None,
        )


def check_queue_depth(health_json: dict, threshold: int) -> CheckResult:
    """Check if bus queue size is below threshold."""
    now = datetime.now(timezone.utc)
    queue_size = health_json.get("queue_size", 0)
    passed = queue_size < threshold
    return CheckResult(
        name="queue_depth",
        passed=passed,
        detail=f"queue_size={queue_size}, threshold={threshold}",
        timestamp=now,
    )


def check_idle(
    health_json: dict,
    threshold_hours: int,
    quiet_start: str,
    quiet_end: str,
) -> CheckResult:
    """Check if the hub has been idle too long (opt-in).

    Passes during quiet hours and when last_message_age_s is null.
    """
    now = datetime.now(timezone.utc)

    last_age = health_json.get("last_message_age_s")
    if last_age is None:
        return CheckResult(
            name="idle",
            passed=True,
            detail="No messages processed yet",
            timestamp=now,
        )

    # Check quiet hours
    current_time = now.strftime("%H:%M")
    if quiet_start <= quiet_end:
        in_quiet = quiet_start <= current_time < quiet_end
    else:
        # Wraps midnight (e.g., 22:00 - 06:00)
        in_quiet = current_time >= quiet_start or current_time < quiet_end

    if in_quiet:
        return CheckResult(
            name="idle",
            passed=True,
            detail=f"Quiet hours ({quiet_start}-{quiet_end})",
            timestamp=now,
        )

    threshold_seconds = threshold_hours * 3600
    passed = last_age < threshold_seconds
    return CheckResult(
        name="idle",
        passed=passed,
        detail=f"last_message_age={last_age:.0f}s, threshold={threshold_seconds}s",
        timestamp=now,
    )


def check_circuits(health_json: dict) -> CheckResult:
    """Check if all circuits are in CLOSED state."""
    now = datetime.now(timezone.utc)
    circuits = health_json.get("circuits", {})
    open_circuits = [
        name for name, info in circuits.items() if info.get("state") != "closed"
    ]
    if open_circuits:
        return CheckResult(
            name="circuits",
            passed=False,
            detail=f"Open circuits: {', '.join(open_circuits)}",
            timestamp=now,
        )
    return CheckResult(
        name="circuits",
        passed=True,
        detail=f"All {len(circuits)} circuits closed",
        timestamp=now,
    )


def check_reaper(health_json: dict) -> CheckResult:
    """Check if the CLI pool reaper is alive and sweeping regularly."""
    now = datetime.now(timezone.utc)
    reaper_alive = health_json.get("reaper_alive", False)
    sweep_age = health_json.get("reaper_last_sweep_age")

    if not reaper_alive:
        return CheckResult(
            name="reaper",
            passed=False,
            detail="Reaper task not running",
            timestamp=now,
        )
    if sweep_age is None:
        return CheckResult(
            name="reaper",
            passed=True,
            detail="Reaper alive, no sweep yet",
            timestamp=now,
        )
    passed = sweep_age <= 120
    return CheckResult(
        name="reaper",
        passed=passed,
        detail=f"last_sweep_age={sweep_age:.0f}s, threshold=120s",
        timestamp=now,
    )


def check_disk(path: str, min_free_gb: int) -> CheckResult:
    """Check if free disk space exceeds minimum threshold."""
    now = datetime.now(timezone.utc)
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    passed = free_gb >= min_free_gb
    return CheckResult(
        name="disk",
        passed=passed,
        detail=f"free={free_gb:.1f}GB, min={min_free_gb}GB",
        timestamp=now,
    )


async def run_checks(config: MonitoringConfig) -> HealthReport:
    """Run all Layer 1 checks and return aggregated report."""
    now = datetime.now(timezone.utc)
    checks: list[CheckResult] = []

    # Check 1: Process liveness (blocking → offload to thread)
    checks.append(await asyncio.to_thread(check_process, config.service_name))

    # Check 2: HTTP health (provides data for checks 3-5)
    http_result, health_json = await check_http_health(
        config.health_endpoint_url,
        config.health_endpoint_timeout_s,
        config.health_secret,
    )
    checks.append(http_result)

    # Checks 3-5 depend on HTTP health succeeding
    if health_json is not None:
        # Check 3: Queue depth
        checks.append(check_queue_depth(health_json, config.queue_depth_threshold))

        # Check 4: Idle (opt-in only)
        if config.idle_check_enabled:
            checks.append(
                check_idle(
                    health_json,
                    config.idle_threshold_hours,
                    config.quiet_start,
                    config.quiet_end,
                )
            )

        # Check 5: Circuit states
        checks.append(check_circuits(health_json))

        # Check 6: Reaper health
        if health_json.get("reaper_alive") is not None:
            checks.append(check_reaper(health_json))

    # Check 7: Disk space (blocking → offload to thread)
    checks.append(
        await asyncio.to_thread(
            check_disk, config.disk_check_path, config.min_disk_free_gb
        )
    )

    failed = [c for c in checks if not c.passed]
    return HealthReport(
        checks=checks,
        all_passed=len(failed) == 0,
        failed_count=len(failed),
        timestamp=now,
    )
