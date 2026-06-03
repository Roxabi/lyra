"""Platform-agnostic KV watch_channels helper.

Reads/watches the ``bot.<platform>.<bot_id>.watch_channels`` key in the
shared ``factory-state`` JetStream KV bucket and exposes three entry points:

- :func:`seed_watch_channels` — one-shot read at startup.
- :func:`start_watch_channels_task` — long-lived watcher task.
- :func:`publish_watch_channels` — hub-side write helper.

The ``factory-state`` bucket is provisioned by the hub only; adapters never
create it (zero ACL change requirement).  Pattern mirrors
``packages/roxabi-nats/src/roxabi_nats/readiness.py::_kv_watch_for_ready``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_BUCKET = "factory-state"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _kv_key(platform: str, bot_id: str) -> str:
    return f"bot.{platform}.{bot_id}.watch_channels"


def _skip_tombstone(entry: Any) -> bool:
    """Return True if the entry is a DEL or PURGE tombstone."""
    return getattr(entry, "operation", None) in {"DEL", "PURGE"}


def _parse_ids(raw: Any) -> frozenset[int]:
    """Coerce each element of *raw* to int, silently skipping invalid values."""
    result: list[int] = []
    for ch in raw:
        try:
            result.append(int(ch))
        except (TypeError, ValueError):
            log.warning("watch_channels: invalid channel id %r — skipping", ch)
    return frozenset(result)


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
    """One-shot KV watch: return the current watch_channels for one bot.

    Opens the ``factory-state`` bucket (must already exist — hub provisions
    it) and waits up to *timeout* seconds for the first non-None, non-tombstone
    entry on ``bot.<platform>.<bot_id>.watch_channels``.

    Returns an empty frozenset when the key is absent, deleted, or no entry
    arrives before the timeout.

    Args:
        js: JetStream context (``nc.jetstream()``).
        platform: Platform string, e.g. ``"discord"``.
        bot_id: Bot identifier string.
        timeout: Maximum seconds to wait for the first real entry.

    Returns:
        Parsed channel IDs as a frozenset of ints.
    """
    key = _kv_key(platform, bot_id)
    kv = await js.key_value(_BUCKET)
    watcher = await kv.watch(key)
    try:
        async with asyncio.timeout(timeout):
            async for entry in watcher:
                if entry is None:
                    continue  # init-done sentinel — no messages pending yet
                if _skip_tombstone(entry):
                    log.debug(
                        "seed_watch_channels: tombstone on %s/%s — empty set",
                        platform,
                        bot_id,
                    )
                    return frozenset()
                ids = _parse_ids(json.loads(entry.value))
                log.debug(
                    "seed_watch_channels: %s/%s → %d channel(s)",
                    platform,
                    bot_id,
                    len(ids),
                )
                return ids
    except TimeoutError:
        log.debug(
            "seed_watch_channels: timeout waiting for %s/%s — empty set",
            platform,
            bot_id,
        )
    finally:
        await watcher.stop()

    return frozenset()


async def _watch_channels_loop(
    js: Any,
    platform: str,
    bot_id: str,
    on_update: Callable[[frozenset[int]], None],
) -> None:
    """Internal coroutine: watch forever and call *on_update* on each change."""
    key = _kv_key(platform, bot_id)
    kv = await js.key_value(_BUCKET)
    watcher = await kv.watch(key)
    try:
        async for entry in watcher:
            if entry is None:
                continue  # init-done sentinel
            if _skip_tombstone(entry):
                log.debug(
                    "watch_channels_task: tombstone on %s/%s — notifying empty set",
                    platform,
                    bot_id,
                )
                on_update(frozenset())
                continue
            ids = _parse_ids(json.loads(entry.value))
            log.debug(
                "watch_channels_task: %s/%s update → %d channel(s)",
                platform,
                bot_id,
                len(ids),
            )
            on_update(ids)
    except asyncio.CancelledError:
        log.debug("watch_channels_task: cancelled for %s/%s", platform, bot_id)
        raise
    finally:
        await watcher.stop()


async def start_watch_channels_task(
    js: Any,
    platform: str,
    bot_id: str,
    on_update: Callable[[frozenset[int]], None],
) -> asyncio.Task[None]:
    """Start a long-lived KV watcher task for one bot's watch_channels.

    On each non-None, non-tombstone KV update, calls ``on_update(frozenset)``
    with the new parsed channel IDs.

    The returned task should be cancelled (and awaited) on adapter teardown.

    Args:
        js: JetStream context (``nc.jetstream()``).
        platform: Platform string, e.g. ``"discord"``.
        bot_id: Bot identifier string.
        on_update: Callable invoked with the new frozenset on every update.

    Returns:
        The running :class:`asyncio.Task`; cancel it to stop the watcher.
    """
    task: asyncio.Task[None] = asyncio.create_task(
        _watch_channels_loop(js, platform, bot_id, on_update),
        name=f"watch_channels:{platform}:{bot_id}",
    )
    return task


async def publish_watch_channels(
    js: Any,
    agent_store: Any,
    bots: list[tuple[str, str]],
) -> None:
    """Write watch_channels for each bot into the ``factory-state`` KV bucket.

    Hub-side helper: reads ``watch_channels`` from the agent store for each
    ``(platform, bot_id)`` pair and publishes it as a JSON-encoded bytes value.

    Args:
        js: JetStream context (``nc.jetstream()``).
        agent_store: Store exposing ``get_bot_settings(platform, bot_id) -> dict``.
        bots: List of ``(platform, bot_id)`` pairs to publish.
    """
    kv = await js.key_value(_BUCKET)
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
