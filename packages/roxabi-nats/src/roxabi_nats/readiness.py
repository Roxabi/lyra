"""NATS readiness probe — hub readiness responder and adapter probe.

The hub writes ``hub.ready = b'true'`` to the ``lyra-state`` KV bucket via
``announce_hub_ready()``; adapters call ``wait_for_hub()`` which reads the
same key via JetStream KV (fast path) or watches for it (watch path).

This approach is compatible with ``allow_responses: false`` ACLs because no
inbox/reply subjects are used.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import Protocol

from nats.aio.client import Client as NATS
from nats.aio.subscription import Subscription

log = logging.getLogger(__name__)

READINESS_SUBJECT = "lyra.system.ready"
PROBE_INTERVAL_S = 0.5
PROBE_TIMEOUT_S = 30.0


class _HasSubscriptionCount(Protocol):
    """Structural type for anything the readiness responder needs from a bus."""

    @property
    def subscription_count(self) -> int: ...


async def announce_hub_ready(nc: NATS) -> None:
    """Write hub.ready = b'true' to lyra-state KV bucket on hub startup.

    Adapters call wait_for_hub() to read this key instead of using a
    request/reply probe — compatible with allow_responses: false ACLs.

    Degrades gracefully if JetStream is not available (logs WARNING, returns).
    """
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BucketNotFoundError

    try:
        js = nc.jetstream()
    except Exception:
        log.warning("Hub KV unavailable — JetStream not enabled")
        return

    try:
        kv = await js.key_value("lyra-state")
    except BucketNotFoundError:
        kv = await js.create_key_value(
            KeyValueConfig(bucket="lyra-state", storage=StorageType.FILE)
        )

    await kv.put("hub.ready", b"true")
    log.info("Hub KV ready announced")


async def start_readiness_responder(
    nc: NATS, buses: Sequence[_HasSubscriptionCount]
) -> Subscription:
    """Subscribe to ``lyra.system.ready`` and reply with hub status on each request.

    Args:
        nc: Already-connected NATS client.
        buses: Sequence of bus-like objects whose ``subscription_count`` values
            are summed into the ``buses`` field of each reply. Uses a
            structural protocol so tests can pass minimal fakes without
            implementing the full ``Bus`` interface.

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
    """Probe hub readiness via JetStream KV instead of request/reply.

    Compatible with allow_responses: false ACLs — no inbox subjects used.
    Returns True if hub.ready key is found, False on timeout or JetStream unavailable.

    Args:
        nc: Already-connected NATS client.
        timeout: Total seconds to wait before giving up. Defaults to
            ``PROBE_TIMEOUT_S``.

    Returns:
        ``True`` if the hub.ready key is found within *timeout*, ``False`` otherwise.
    """
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BucketNotFoundError, KeyNotFoundError

    deadline = time.monotonic() + timeout

    # Open or create the KV bucket
    try:
        js = nc.jetstream()
        try:
            kv = await js.key_value("lyra-state")
        except BucketNotFoundError:
            kv = await js.create_key_value(
                KeyValueConfig(bucket="lyra-state", storage=StorageType.FILE)
            )
    except Exception:
        log.warning("wait_for_hub: JetStream not enabled — skipping probe")
        return False

    # Fast path: key already present
    try:
        entry = await kv.get("hub.ready")
        if entry.value == b"true":
            log.info("Hub ready (KV immediate)")
            return True
    except KeyNotFoundError:
        pass  # fall through to watch

    # Watch path: block until key appears or timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        log.warning(
            "Hub readiness probe timed out after %ss — starting anyway (graceful degradation)",
            timeout,
        )
        return False

    try:
        watcher = await kv.watch("hub.ready")
        try:
            async with asyncio.timeout(remaining):
                async for entry in watcher:
                    if entry is None:
                        continue  # init-done sentinel — no messages pending yet
                    if entry.value == b"true":
                        log.info("Hub ready (KV watch)")
                        return True
        except TimeoutError:
            pass
        finally:
            await watcher.stop()
    except Exception:
        log.exception("wait_for_hub: unexpected error during KV watch")

    log.warning(
        "Hub readiness probe timed out after %ss — starting anyway (graceful degradation)",
        timeout,
    )
    return False
