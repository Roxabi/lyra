"""NATS readiness probe — hub readiness responder and adapter probe.

The hub registers a responder on ``lyra.system.ready``; adapters send a
request loop until the hub replies or the probe times out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import nats.errors
from nats.aio.client import Client as NATS
from nats.aio.subscription import Subscription

from lyra.core.bus import Bus

log = logging.getLogger(__name__)

READINESS_SUBJECT = "lyra.system.ready"
PROBE_INTERVAL_S = 0.5
PROBE_TIMEOUT_S = 30.0


async def start_readiness_responder(
    nc: NATS, buses: list[Bus[Any]]
) -> Subscription:
    """Subscribe to ``lyra.system.ready`` and reply with hub status on each request.

    Args:
        nc: Already-connected NATS client.
        buses: List of bus instances whose ``subscription_count`` values are
            summed into the ``buses`` field of each reply.

    Returns:
        The NATS Subscription object so callers can unsubscribe when needed.
    """
    started_at = time.monotonic()

    async def _handler(msg) -> None:  # type: ignore[no-untyped-def]
        # Ignore stray publishes with no reply subject — nc.publish("") would raise.
        if not msg.reply:
            return
        uptime_s = time.monotonic() - started_at
        bus_count = sum(b.subscription_count for b in buses)
        payload = json.dumps(
            {"status": "ready", "uptime_s": round(uptime_s, 3), "buses": bus_count}
        ).encode()
        await nc.publish(msg.reply, payload)

    sub = await nc.subscribe(READINESS_SUBJECT, cb=_handler)
    log.info("Hub ready — accepting readiness probes on %s", READINESS_SUBJECT)
    return sub


async def wait_for_hub(
    nc: NATS,
    *,
    timeout: float = PROBE_TIMEOUT_S,
) -> bool:
    """Probe the hub readiness subject until a reply is received or timeout expires.

    Args:
        nc: Already-connected NATS client.
        timeout: Total seconds to wait before giving up. Defaults to
            ``PROBE_TIMEOUT_S``.

    Returns:
        ``True`` if the hub replied within *timeout*, ``False`` otherwise.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        per_call = min(PROBE_INTERVAL_S, remaining)
        try:
            await nc.request(READINESS_SUBJECT, b"", timeout=per_call)
            log.info("Hub readiness confirmed — starting polling")
            return True
        except nats.errors.TimeoutError:
            pass
        except nats.errors.NoRespondersError:
            pass
        except Exception:
            log.exception("wait_for_hub: unexpected error during probe")

        # Brief sleep between probes so we don't hammer NATS on NoRespondersError
        await asyncio.sleep(PROBE_INTERVAL_S)

    log.warning(
        "Hub readiness probe timed out after %ss — "
        "starting anyway (graceful degradation)",
        timeout,
    )
    return False
