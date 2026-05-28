"""Outbound-audio JetStream health checks (#1482 T11).

Two probes:
  check_audio_consumer_lag  — polls NATS HTTP /jsz for LYRA_OUTBOUND_AUDIO
                               consumer num_pending and oldest-message age
  check_audio_stream_usage  — polls NATS HTTP /jsz for stream num_bytes vs
                               max_bytes (stream-fullness alert)

Both use the NATS HTTP monitoring API at nats_monitor_url (default
http://127.0.0.1:8222), the same base URL used by check_nats_varz in
checks_varz.py.

Subject grammar: lyra.outbound.audio.<platform>.<bot_id>
Stream: LYRA_OUTBOUND_AUDIO  (max_age=86400s, max_bytes=32MiB, AckWait=90s,
                               MaxDeliver=5)
Consumers: outbound-audio-telegram, outbound-audio-discord (durable pull)

Alert thresholds (configurable via MonitoringConfig):
  - consumer lag: num_pending > audio_lag_pending_threshold (default: 50)
  - oldest unacked age > audio_lag_age_warn_s (default: 72000 s = 20 h,
    approaching the 24 h max_age window)
  - stream fullness > audio_stream_usage_warn_pct of max_bytes (default: 80%)

Check names:
  "audio:consumer_lag"     — consumer num_pending / oldest-message age
  "audio:stream_usage"     — stream bytes vs max_bytes
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .models import CheckResult

log = logging.getLogger(__name__)

_STREAM_NAME = "LYRA_OUTBOUND_AUDIO"
# Consumers to check: outbound-audio-<platform>
_CONSUMER_PREFIXES = ("outbound-audio-",)

# Stream max_bytes from stream_setup.py (32 MiB); used as fallback when the
# NATS API response omits config.max_bytes.
_FALLBACK_MAX_BYTES = 32 * 1024 * 1024


def _consumer_age_failure(
    consumer: dict,
    name: str,
    num_pending: int,
    now: datetime,
    lag_age_warn_s: int,
) -> str | None:
    """Return a failure string if ack_floor age exceeds threshold, else None.

    Age check: when pending > 0, ack_floor.last_active is the timestamp of the
    last acknowledged message delivery. Messages above the ack_floor have not
    been acked yet; if (now - last_active) > lag_age_warn_s the batch is stale
    and approaching the 24 h stream MaxAge bound.
    """
    if num_pending == 0:
        return None
    ack_floor = consumer.get("ack_floor") or {}
    last_active_str = ack_floor.get("last_active")
    if not last_active_str:
        return None
    try:
        last_active = datetime.fromisoformat(last_active_str.replace("Z", "+00:00"))
        age_s = (now - last_active).total_seconds()
        if age_s > lag_age_warn_s:
            return (
                f"{name}: oldest unacked age={age_s:.0f}s"
                f" > warn_threshold={lag_age_warn_s}s"
            )
    except (ValueError, TypeError):
        pass
    return None


async def check_audio_consumer_lag(
    nats_monitor_url: str,
    *,
    lag_pending_threshold: int = 50,
    lag_age_warn_s: int = 72000,
    timeout: int = 5,
) -> CheckResult:
    """Check LYRA_OUTBOUND_AUDIO consumer lag via NATS HTTP /jsz.

    Queries ``/jsz?consumers=1&name=LYRA_OUTBOUND_AUDIO`` and iterates
    over all consumers whose names start with ``outbound-audio-``.

    Fails if any consumer has:
      - ``num_pending`` > ``lag_pending_threshold`` (default: 50), OR
      - oldest unacked message age (``num_pending > 0`` and
        ``ack_floor.last_active`` age) > ``lag_age_warn_s`` (default: 72000 s
        / 20 h — approaching the 24 h MaxAge bound).

    If the stream or consumers are not yet provisioned the check passes
    (not-yet-started is not the same as broken).
    """
    now = datetime.now(timezone.utc)
    url = nats_monitor_url.rstrip("/") + f"/jsz?consumers=1&name={_STREAM_NAME}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout)
        if resp.status_code == 404:
            # /jsz?name=<stream> returns 404 when NATS has no JetStream
            # enabled or the stream was never provisioned. Treat as skip,
            # not a failure — pre-deploy state is not broken state.
            return CheckResult(
                name="audio:consumer_lag",
                passed=True,
                detail=f"stream {_STREAM_NAME} not yet provisioned — skipping",
                timestamp=now,
            )
        if resp.status_code != 200:
            return CheckResult(
                name="audio:consumer_lag",
                passed=False,
                detail=f"HTTP {resp.status_code} from {url}",
                timestamp=now,
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        return CheckResult(
            name="audio:consumer_lag",
            passed=False,
            detail=type(exc).__name__,
            timestamp=now,
        )

    streams: list[dict] = data.get("streams") or []
    stream_data = next((s for s in streams if s.get("name") == _STREAM_NAME), None)
    if stream_data is None:
        return CheckResult(
            name="audio:consumer_lag",
            passed=True,
            detail=f"stream {_STREAM_NAME} absent from jsz response — skipping",
            timestamp=now,
        )

    consumers: list[dict] = stream_data.get("consumers") or []
    audio_consumers = [
        c
        for c in consumers
        if any(c.get("name", "").startswith(pfx) for pfx in _CONSUMER_PREFIXES)
    ]
    if not audio_consumers:
        return CheckResult(
            name="audio:consumer_lag",
            passed=True,
            detail="no outbound-audio-* consumers found — skipping",
            timestamp=now,
        )

    failures: list[str] = []
    details: list[str] = []
    for consumer in audio_consumers:
        name = consumer.get("name", "<unknown>")
        num_pending = int(consumer.get("num_pending", 0))
        details.append(f"{name}: pending={num_pending}")

        if num_pending > lag_pending_threshold:
            failures.append(
                f"{name}: num_pending={num_pending} > threshold={lag_pending_threshold}"
            )

        age_fail = _consumer_age_failure(
            consumer, name, num_pending, now, lag_age_warn_s
        )
        if age_fail:
            failures.append(age_fail)

    if failures:
        return CheckResult(
            name="audio:consumer_lag",
            passed=False,
            detail="; ".join(failures),
            timestamp=now,
        )
    return CheckResult(
        name="audio:consumer_lag",
        passed=True,
        detail=", ".join(details) if details else "OK",
        timestamp=now,
    )


async def check_audio_stream_usage(
    nats_monitor_url: str,
    *,
    warn_pct: int = 80,
    timeout: int = 5,
) -> CheckResult:
    """Check LYRA_OUTBOUND_AUDIO stream byte usage vs max_bytes.

    Queries ``/jsz?name=LYRA_OUTBOUND_AUDIO`` (no consumers detail needed).
    Fails when ``state.bytes / config.max_bytes >= warn_pct / 100``.

    Falls back to the compiled-in 32 MiB constant if ``config.max_bytes``
    is absent from the API response.
    """
    now = datetime.now(timezone.utc)
    url = nats_monitor_url.rstrip("/") + f"/jsz?name={_STREAM_NAME}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout)
        if resp.status_code == 404:
            # Same reasoning as check_audio_consumer_lag: pre-deploy state is not
            # a failure. /jsz returns 404 when JetStream is disabled or the stream
            # has not been provisioned yet.
            return CheckResult(
                name="audio:stream_usage",
                passed=True,
                detail=f"stream {_STREAM_NAME} not yet provisioned — skipping",
                timestamp=now,
            )
        if resp.status_code != 200:
            return CheckResult(
                name="audio:stream_usage",
                passed=False,
                detail=f"HTTP {resp.status_code} from {url}",
                timestamp=now,
            )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        return CheckResult(
            name="audio:stream_usage",
            passed=False,
            detail=type(exc).__name__,
            timestamp=now,
        )

    streams: list[dict] = data.get("streams") or []
    stream_data = next((s for s in streams if s.get("name") == _STREAM_NAME), None)
    if stream_data is None:
        return CheckResult(
            name="audio:stream_usage",
            passed=True,
            detail=f"stream {_STREAM_NAME} absent from jsz response — skipping",
            timestamp=now,
        )

    state = stream_data.get("state") or {}
    config = stream_data.get("config") or {}
    current_bytes = int(state.get("bytes", 0))
    max_bytes = int(config.get("max_bytes", 0)) or _FALLBACK_MAX_BYTES

    used_pct = (current_bytes / max_bytes) * 100
    passed = used_pct < warn_pct
    level = "WARNING" if not passed else "OK"
    detail = (
        f"{level}: used={used_pct:.1f}% ({current_bytes}B / {max_bytes}B),"
        f" threshold={warn_pct}%"
    )
    return CheckResult(
        name="audio:stream_usage",
        passed=passed,
        detail=detail,
        timestamp=now,
    )
