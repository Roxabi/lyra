"""Platform-agnostic KV watch_channels helper.

Reads the ``bot.<platform>.<bot_id>.watch_channels`` key in the shared
``factory-state`` JetStream KV bucket and exposes two entry points:

- :func:`seed_watch_channels` — one-shot read at startup (via ``kv.get``).
- :func:`publish_watch_channels` — hub-side write helper.

The ``factory-state`` bucket is provisioned by the hub only; adapters never
create it (zero ACL change requirement).

NOTE: A live ``kv.watch``-based updater (``start_watch_channels_task``) was
intentionally removed here.  It will be re-added with its ACL grants when
#1720 (runtime watch_channels writes) lands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

_BUCKET = "factory-state"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _kv_key(platform: str, bot_id: str) -> str:
    return f"bot.{platform}.{bot_id}.watch_channels"


def _parse_ids(raw: Any) -> frozenset[int]:
    """Coerce each element of *raw* to int, silently skipping invalid values."""
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    result: list[int] = []
    for ch in raw:
        try:
            result.append(int(ch))
        except (TypeError, ValueError):
            log.warning("watch_channels: invalid channel id %r — skipping", ch)
    return frozenset(result)


async def _open_or_create_kv(js: Any) -> Any:
    """Open or create the factory-state KV bucket (hub-only provisioner path).

    Handles the cold-boot race: bucket may not exist on the first hub start
    because announce_hub_ready (which calls this indirectly) runs *after*
    publish_watch_channels.  Only publish_watch_channels uses this helper;
    adapter-side readers use plain js.key_value() (bind-only, relying on
    wait_for_hub to guarantee the bucket exists first).
    """
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BadRequestError, BucketNotFoundError

    try:
        return await js.key_value(_BUCKET)
    except BucketNotFoundError:
        pass

    try:
        return await js.create_key_value(
            KeyValueConfig(bucket=_BUCKET, storage=StorageType.FILE)
        )
    except BadRequestError:
        # Lost the creation race — another process created it; open it.
        return await js.key_value(_BUCKET)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def seed_watch_channels(
    js: Any,
    platform: str,
    bot_id: str,
    *,
    timeout: float = 2.0,
) -> frozenset[int]:
    """One-shot KV read: return the current watch_channels for one bot.

    Binds the ``factory-state`` bucket (must already exist — hub provisions
    it) and fetches the key ``bot.<platform>.<bot_id>.watch_channels`` via
    ``kv.get``.  Uses ``$JS.API.STREAM.MSG.GET`` under the hood (adapters hold
    this grant since #1572; no ephemeral-consumer ACL needed).

    Returns an empty frozenset when the key is absent, deleted/purged, the
    payload is malformed, or the get times out.

    Args:
        js: JetStream context (``nc.jetstream()``).
        platform: Platform string, e.g. ``"discord"``.
        bot_id: Bot identifier string.
        timeout: Maximum seconds to wait for the KV get.

    Returns:
        Parsed channel IDs as a frozenset of ints.
    """
    from nats.js.errors import KeyNotFoundError

    key = _kv_key(platform, bot_id)
    kv = await js.key_value(_BUCKET)
    try:
        async with asyncio.timeout(timeout):
            entry = await kv.get(key)
    except KeyNotFoundError:
        # Covers missing key, DEL tombstone, and PURGE tombstone — nats-py
        # re-raises KeyDeletedError as KeyNotFoundError inside kv.get().
        log.debug(
            "seed_watch_channels: key not found for %s/%s — empty set",
            platform,
            bot_id,
        )
        return frozenset()
    except TimeoutError:
        log.debug(
            "seed_watch_channels: timeout waiting for %s/%s — empty set",
            platform,
            bot_id,
        )
        return frozenset()

    try:
        ids = _parse_ids(json.loads(entry.value))
    except (json.JSONDecodeError, TypeError):
        log.warning(
            "seed_watch_channels: bad KV payload for %s/%s — empty set",
            platform,
            bot_id,
        )
        return frozenset()

    log.debug(
        "seed_watch_channels: %s/%s → %d channel(s)",
        platform,
        bot_id,
        len(ids),
    )
    return ids


async def publish_watch_channels(
    js: Any,
    agent_store: Any,
    bots: list[tuple[str, str]],
) -> None:
    """Write watch_channels for each bot into the ``factory-state`` KV bucket.

    Hub-side helper: reads ``watch_channels`` from the agent store for each
    ``(platform, bot_id)`` pair and publishes it as a JSON-encoded bytes value.

    Uses create-or-open so the hub can run publish_watch_channels before
    announce_hub_ready on a fresh NATS deployment (cold-boot safety).

    Args:
        js: JetStream context (``nc.jetstream()``).
        agent_store: Store exposing ``get_bot_settings(platform, bot_id) -> dict``.
        bots: List of ``(platform, bot_id)`` pairs to publish.
    """
    kv = await _open_or_create_kv(js)
    for platform, bot_id in bots:
        settings = agent_store.get_bot_settings(platform, bot_id)
        ids: list[Any] = settings.get("watch_channels", [])
        key = _kv_key(platform, bot_id)
        await kv.put(key, json.dumps(ids).encode())
        log.debug(
            "publish_watch_channels: wrote %d channel(s) for %s/%s",
            len(ids),
            platform,
            bot_id,
        )
