"""Log-scanning health checks — reads container logs via podman."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from .models import CheckResult

_LOG_EXCEPTIONS = (
    subprocess.TimeoutExpired,
    FileNotFoundError,
    subprocess.CalledProcessError,
)


def check_nats_log_errors(
    container_name: str, max_age_minutes: int
) -> CheckResult:
    """Check NATS container logs for permissions violation errors.

    Runs `podman logs --since {max_age_minutes}m {container_name}` and counts
    lines containing "permissions violation". Any count > 0 is a failure.
    """
    now = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            ["podman", "logs", "--since", f"{max_age_minutes}m", container_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        count = sum(
            1 for line in combined.splitlines() if "permissions violation" in line
        )
        if count > 0:
            return CheckResult(
                name="nats:permissions_violation",
                passed=False,
                detail=f"permissions violation: {count} in last {max_age_minutes}m",
                timestamp=now,
            )
        return CheckResult(
            name="nats:permissions_violation",
            passed=True,
            detail=f"0 violations in last {max_age_minutes}m",
            timestamp=now,
        )
    except _LOG_EXCEPTIONS as exc:
        return CheckResult(
            name="nats:permissions_violation",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )


def check_hub_stream_gen_timeout(
    container_name: str, max_age_minutes: int, threshold: int
) -> CheckResult:
    """Check hub container logs for _stream_gen timeout occurrences.

    Runs `podman logs --since {max_age_minutes}m {container_name}` and counts
    lines containing "_stream_gen timeout" (case-insensitive). Fails when
    count >= threshold.
    """
    now = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            ["podman", "logs", "--since", f"{max_age_minutes}m", container_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        count = sum(
            1 for line in combined.splitlines()
            if "_stream_gen timeout" in line.lower()
        )
        if count >= threshold:
            return CheckResult(
                name="hub:stream_gen_timeout",
                passed=False,
                detail=(
                    f"_stream_gen timeout: {count} in last {max_age_minutes}m"
                    f" (threshold={threshold})"
                ),
                timestamp=now,
            )
        return CheckResult(
            name="hub:stream_gen_timeout",
            passed=True,
            detail=(
                f"{count} timeouts in last {max_age_minutes}m"
                f" (threshold={threshold})"
            ),
            timestamp=now,
        )
    except _LOG_EXCEPTIONS as exc:
        return CheckResult(
            name="hub:stream_gen_timeout",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )
