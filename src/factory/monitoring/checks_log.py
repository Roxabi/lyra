"""Log-scanning health checks — reads container logs via podman."""

from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Protocol

import httpx

from ._errors import _MONITORING_HTTP_ERRORS
from .models import CheckResult

_LOG_EXCEPTIONS = (
    subprocess.TimeoutExpired,
    FileNotFoundError,
    subprocess.CalledProcessError,
)

# Loki's query_range response is untrusted JSON shape, not just untrusted
# transport: beyond the shared HTTP/parse errors (_MONITORING_HTTP_ERRORS —
# httpx.HTTPError, ValueError incl. JSONDecodeError, TypeError, OSError),
# a malformed-but-200-OK body can also raise KeyError (missing "data"/
# "result"/"values" keys) or IndexError (a "values" entry with < 2 elements).
# All of these must become LogFetchError, never escape raw — an unhandled
# exception here kills LogMonitorLoop.run_once() and, on a deterministic
# response shape, exhausts systemd's Restart=on-failure burst limit.
_LOKI_FETCH_ERRORS = (*_MONITORING_HTTP_ERRORS, KeyError, IndexError)


class LogFetchError(Exception):
    """Raised by any LogFetcher implementation on fetch failure."""


class LogFetcher(Protocol):
    def fetch(self, container_name: str, since_minutes: int, pattern: str) -> str: ...


class SubprocessLogFetcher:
    """Default fetcher — shells out to `podman logs`, behavior unchanged from before
    this refactor."""

    def fetch(self, container_name: str, since_minutes: int, pattern: str) -> str:
        # pattern unused: podman logs --since has no line-count cap, so there is no
        # truncation risk to guard against locally — kept only for LogFetcher protocol
        # conformance with LokiLogFetcher (T2), which DOES need it.
        try:
            result = subprocess.run(
                ["podman", "logs", "--since", f"{since_minutes}m", container_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except _LOG_EXCEPTIONS as e:
            raise LogFetchError(str(e)) from e
        return result.stdout + result.stderr


class LokiLogFetcher:
    """Fetches container logs from Loki (ADR-093) instead of local `podman logs`."""

    def __init__(self, loki_url: str = "http://factory-loki:3100") -> None:
        self.loki_url = loki_url

    def fetch(self, container_name: str, since_minutes: int, pattern: str) -> str:
        # Server-side |~ "(?i)..." filter is load-bearing, not cosmetic: Loki's
        # query_range returns at most `limit` lines, NEWEST-FIRST, within the window —
        # unlike `podman logs --since` (no cap). On a busy container this could push
        # true violations outside an unfiltered fetch. Case-insensitive regex is a
        # safe superset for both checks; exact counting still happens client-side
        # after fetch (unchanged from today).
        # LogQL/RE2 rejects re.escape's `\ ` for literal spaces — keep spaces literal.
        escaped = re.escape(pattern).replace(r"\ ", " ")
        query = f'{{systemd_unit="{container_name}.service"}} |~ "(?i){escaped}"'
        now_ns = time.time_ns()
        start_ns = now_ns - since_minutes * 60 * 1_000_000_000
        params = {
            "query": query,
            "start": start_ns,
            "end": now_ns,
            "limit": 5000,
        }
        try:
            resp = httpx.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            lines = [
                value[1]
                for result in data["data"]["result"]
                for value in result["values"]
            ]
        except _LOKI_FETCH_ERRORS as e:
            raise LogFetchError(str(e)) from e
        return "\n".join(lines)


def check_nats_log_errors(
    container_name: str,
    max_age_minutes: int,
    fetcher: LogFetcher = SubprocessLogFetcher(),
) -> CheckResult:
    """Check NATS container logs for permissions violation errors.

    Fetches logs via `fetcher` (default: local `podman logs --since`) and
    counts lines containing "permissions violation". Any count > 0 is a
    failure.
    """
    now = datetime.now(timezone.utc)
    try:
        combined = fetcher.fetch(
            container_name, max_age_minutes, pattern="permissions violation"
        )
    except LogFetchError as exc:
        return CheckResult(
            name="nats:permissions_violation",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )
    count = sum(1 for line in combined.splitlines() if "permissions violation" in line)
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


def check_hub_dict_stream_gen_timeout(
    container_name: str,
    max_age_minutes: int,
    threshold: int,
    fetcher: LogFetcher = SubprocessLogFetcher(),
) -> CheckResult:
    """Check hub container logs for _dict_stream_gen timeout occurrences.

    Fetches logs via `fetcher` (default: local `podman logs --since`) and
    counts lines containing "_dict_stream_gen timeout" (case-insensitive).
    Fails when count >= threshold.
    """
    now = datetime.now(timezone.utc)
    try:
        combined = fetcher.fetch(
            container_name, max_age_minutes, pattern="_dict_stream_gen timeout"
        )
    except LogFetchError as exc:
        return CheckResult(
            name="hub:dict_stream_gen_timeout",
            passed=False,
            detail=str(exc),
            timestamp=now,
        )
    count = sum(
        1
        for line in combined.splitlines()
        if "_dict_stream_gen timeout" in line.lower()
    )
    if count >= threshold:
        return CheckResult(
            name="hub:dict_stream_gen_timeout",
            passed=False,
            detail=(
                f"_dict_stream_gen timeout: {count} in last {max_age_minutes}m"
                f" (threshold={threshold})"
            ),
            timestamp=now,
        )
    return CheckResult(
        name="hub:dict_stream_gen_timeout",
        passed=True,
        detail=(f"{count} timeouts in last {max_age_minutes}m (threshold={threshold})"),
        timestamp=now,
    )
