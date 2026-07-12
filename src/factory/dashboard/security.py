"""Control-plane security audit + rate limit (ADR-103 Blocks 13–14)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any

log = logging.getLogger("factory.audit.security")

__all__ = [
    "audit_security",
    "check_rate_limit",
    "client_key",
]

# Sliding window: max *limit* events per *window_s* for a given key.
_DEFAULT_WINDOW_S = 60  # const-ok: rate-limit window
_DEFAULT_LIMIT = 20  # const-ok: max attempts per window
_buckets: dict[str, deque[float]] = defaultdict(deque)


def audit_security(event: str, **fields: Any) -> None:
    """Emit a structured security audit line (logs → journal / NATS consumers).

    Subject convention: ``factory.audit.security.<event>`` in the log message
    so ops can grep / stream filter without a dedicated bus yet.
    """
    safe = {k: v for k, v in fields.items() if v is not None}
    # Never log secrets
    for banned in ("password", "token", "secret", "cookie"):
        safe.pop(banned, None)
    log.info(
        "factory.audit.security.%s %s",
        event,
        " ".join(f"{k}={v!r}" for k, v in sorted(safe.items())),
    )


def client_key(request: Any, *, suffix: str = "") -> str:
    """Rate-limit key from client IP (or fallback) + optional suffix."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) or "unknown"
    return f"{host}:{suffix}" if suffix else host


def check_rate_limit(
    key: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    window_s: float = _DEFAULT_WINDOW_S,
) -> bool:
    """Return True if allowed; False if the key is over limit.

    In-memory per-process only — good enough for single-instance dashboard.
    """
    now = time.monotonic()
    bucket = _buckets[key]
    cutoff = now - window_s
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True
