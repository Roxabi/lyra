"""Infrastructure resource checks: NATS /varz HTTP polling and disk space."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

import httpx

from .models import CheckResult


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


async def check_nats_varz(url: str, state_file: str, timeout: int = 5) -> CheckResult:
    """Poll NATS /varz for auth_errors and slow_consumers with delta tracking.

    Counters are cumulative since NATS start. A state file tracks last-seen values
    so only new increments trigger failures. Negative delta (NATS restarted) silently
    reinitializes the baseline without alerting.
    """
    now = datetime.now(timezone.utc)
    varz_url = url.rstrip("/") + "/varz"
    state_path = os.path.expanduser(state_file)

    # Load last-seen state
    last: dict[str, int] = {}
    try:
        with open(state_path) as f:
            last = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Fetch /varz
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(varz_url, timeout=timeout)
        if resp.status_code != 200:
            return CheckResult(
                name="nats:varz",
                passed=False,
                detail=f"HTTP {resp.status_code} from {varz_url}",
                timestamp=now,
            )
        data = resp.json()
    except Exception as exc:
        return CheckResult(
            name="nats:varz",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )

    current_auth = int(data.get("auth_errors", 0))
    current_slow = int(data.get("slow_consumers", 0))

    last_auth = last.get("auth_errors", current_auth)
    last_slow = last.get("slow_consumers", current_slow)

    delta_auth = current_auth - last_auth
    delta_slow = current_slow - last_slow

    # Negative delta → NATS restarted; reinitialize baseline silently
    if delta_auth < 0:
        delta_auth = 0
    if delta_slow < 0:
        delta_slow = 0

    # Persist current values
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    try:
        with open(state_path, "w") as f:
            json.dump({"auth_errors": current_auth, "slow_consumers": current_slow}, f)
    except OSError:
        pass

    failures = []
    if delta_auth > 0:
        failures.append(f"auth_errors +{delta_auth}")
    if delta_slow > 0:
        failures.append(f"slow_consumers +{delta_slow}")

    if failures:
        return CheckResult(
            name="nats:varz",
            passed=False,
            detail="; ".join(failures),
            timestamp=now,
        )
    return CheckResult(
        name="nats:varz",
        passed=True,
        detail=f"auth_errors={current_auth} slow_consumers={current_slow} (no new)",
        timestamp=now,
    )
