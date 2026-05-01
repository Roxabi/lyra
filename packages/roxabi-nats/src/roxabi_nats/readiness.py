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
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from nats.js.kv import KeyValue

from nats.aio.client import Client as NATS
from nats.aio.subscription import Subscription

log = logging.getLogger(__name__)

READINESS_SUBJECT = "lyra.system.ready"
PROBE_TIMEOUT_S = 30.0


class _HasSubscriptionCount(Protocol):
    """Structural type for anything the readiness responder needs from a bus."""

    @property
    def subscription_count(self) -> int: ...


async def _open_or_create_lyra_state_kv(js: object) -> KeyValue:
    """Open or create the lyra-state KV bucket (hub-only).

    Handles the concurrent-creation race: if create_key_value raises
    BadRequestError (another process won the race between our key_value miss
    and this create call), falls back to opening the existing bucket.
    """
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BadRequestError, BucketNotFoundError

    try:
        return await js.key_value("lyra-state")  # type: ignore[union-attr]
    except BucketNotFoundError:
        pass

    try:
        return await js.create_key_value(  # type: ignore[union-attr]
            KeyValueConfig(bucket="lyra-state", storage=StorageType.FILE)
        )
    except BadRequestError:
        # Lost the creation race — bucket exists now; open it.
        return await js.key_value("lyra-state")  # type: ignore[union-attr]


async def announce_hub_ready(nc: NATS) -> None:
    """Write hub.ready = b'true' to lyra-state KV bucket on hub startup.

    Adapters call wait_for_hub() to read this key instead of using a
    request/reply probe — compatible with allow_responses: false ACLs.

    Degrades gracefully if JetStream is not available (logs WARNING, returns).
    """
    from nats.js.errors import ServiceUnavailableError

    try:
        js = nc.jetstream()
        kv = await _open_or_create_lyra_state_kv(js)
    except ServiceUnavailableError:
        log.warning("Hub KV unavailable — JetStream not enabled")
        return
    except Exception:
        log.exception("announce_hub_ready: unexpected error opening lyra-state KV")
        return

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


_TIMEOUT_MSG = (
    "Hub readiness probe timed out after %ss — starting anyway (graceful degradation)"
)


async def _kv_watch_for_ready(kv: KeyValue, remaining: float) -> bool:
    """Block on KV watch until hub.ready=b'true' appears or timeout expires."""
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
    except Exception:
        log.exception("wait_for_hub: unexpected error during KV watch")
    finally:
        await watcher.stop()
    return False


async def _open_kv_with_retry(js: object, deadline: float) -> KeyValue | None:
    """Wait for hub to provision the lyra-state bucket, retrying on absence.

    Returns the open KeyValue handle, or None when the deadline expires before
    the bucket appears. Raises on errors other than BucketNotFoundError (e.g.
    ServiceUnavailableError when JetStream is disabled).
    """
    from nats.js.errors import BucketNotFoundError

    while True:
        try:
            return await js.key_value("lyra-state")  # type: ignore[union-attr]
        except BucketNotFoundError:
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                return None
            await asyncio.sleep(0.5)


async def wait_for_hub(
    nc: NATS,
    *,
    timeout: float = PROBE_TIMEOUT_S,
) -> bool:
    """Probe hub readiness via JetStream KV instead of request/reply.

    Compatible with allow_responses: false ACLs — no inbox subjects used.
    Returns True if hub.ready key is found, False on timeout or JetStream unavailable.

    Adapters must not create the lyra-state bucket — only the hub provisions it
    via announce_hub_ready(). If the bucket is absent, this function retries
    key_value() until the hub provisions it or the timeout expires.

    Args:
        nc: Already-connected NATS client.
        timeout: Total seconds to wait before giving up. Defaults to
            ``PROBE_TIMEOUT_S``.

    Returns:
        ``True`` if the hub.ready key is found within *timeout*, ``False`` otherwise.
    """
    from nats.js.errors import KeyNotFoundError

    deadline = time.monotonic() + timeout
    js = nc.jetstream()

    try:
        kv = await _open_kv_with_retry(js, deadline)
    except Exception:
        log.warning("wait_for_hub: JetStream not enabled — skipping probe")
        return False

    if kv is None:
        log.warning(_TIMEOUT_MSG, timeout)
        return False

    try:
        entry = await kv.get("hub.ready")
        if entry.value == b"true":
            log.info("Hub ready (KV immediate)")
            return True
    except KeyNotFoundError:
        pass  # fall through to watch

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        log.warning(_TIMEOUT_MSG, timeout)
        return False

    if await _kv_watch_for_ready(kv, remaining):
        return True

    log.warning(_TIMEOUT_MSG, timeout)
    return False
