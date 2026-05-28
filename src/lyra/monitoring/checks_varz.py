"""Infrastructure resource checks: NATS /varz HTTP polling and disk space."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone

import httpx

from .models import CheckResult

log = logging.getLogger(__name__)


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


def check_disk_pct(path: str, warning_pct: int, critical_pct: int) -> CheckResult:
    """Check disk usage percentage against warning and critical thresholds."""
    now = datetime.now(timezone.utc)
    usage = shutil.disk_usage(path)
    total = usage.total
    used_pct = (usage.used / total) * 100 if total else 0
    passed = used_pct < warning_pct
    if used_pct >= critical_pct:
        level = "CRITICAL"
    elif used_pct >= warning_pct:
        level = "WARNING"
    else:
        level = "OK"
    detail = (
        f"{level}: used={used_pct:.1f}%,"
        f" warning={warning_pct}%, critical={critical_pct}%"
    )
    return CheckResult(
        name="disk_pct",
        passed=passed,
        detail=detail,
        timestamp=now,
    )


def check_inode_pct(path: str, warning_pct: int, critical_pct: int) -> CheckResult:
    """Check inode usage percentage against warning and critical thresholds."""
    now = datetime.now(timezone.utc)
    st = os.statvfs(path)
    total = st.f_files
    used = total - st.f_ffree if total else 0
    used_pct = (used / total) * 100 if total else 0
    passed = used_pct < warning_pct
    if used_pct >= critical_pct:
        level = "CRITICAL"
    elif used_pct >= warning_pct:
        level = "WARNING"
    else:
        level = "OK"
    detail = (
        f"{level}: used={used_pct:.1f}%,"
        f" warning={warning_pct}%, critical={critical_pct}%"
    )
    return CheckResult(
        name="inode_pct",
        passed=passed,
        detail=detail,
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

    # Fetch /varz — also catches ValueError/TypeError from int() on unexpected schema
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
        current_auth = int(data.get("auth_errors", 0))
        current_slow = int(data.get("slow_consumers", 0))
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        return CheckResult(
            name="nats:varz",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )

    # Negative delta → NATS restarted; max(0, ...) reinitializes baseline silently
    delta_auth = max(0, current_auth - last.get("auth_errors", current_auth))
    delta_slow = max(0, current_slow - last.get("slow_consumers", current_slow))

    # Persist current values
    parent = os.path.dirname(state_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(state_path, "w") as f:
            json.dump({"auth_errors": current_auth, "slow_consumers": current_slow}, f)
    except OSError as exc:
        log.warning("nats:varz state write failed: %s", exc)

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
