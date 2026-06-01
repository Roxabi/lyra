"""NATS KV wrapper for lyra-bot-settings — watch_channels and future per-bot config.

Hub is the sole provisioner (creates the KV bucket). Adapters bind-only
(js.key_value) and watch for updates.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import BadRequestError, KeyNotFoundError
from nats.js.kv import KeyValue

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

KV_BUCKET = "lyra-bot-settings"


def _watch_channels_key(bot_id: str) -> str:
    return f"watch_channels.discord.{bot_id}"


def _parse_watch_channels(raw: bytes | None) -> frozenset[int]:
    if raw is None:
        return frozenset()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        log.warning(
            "bot_settings_kv: invalid JSON for watch_channels — treating as empty"
        )
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    valid: list[int] = []
    for item in data:
        try:
            valid.append(int(item))
        except (ValueError, TypeError):
            log.warning("bot_settings_kv: invalid channel id %r — skipping", item)
    return frozenset(valid)


async def ensure_kv(js: JetStreamContext) -> KeyValue:
    """Create or bind KV bucket lyra-bot-settings idempotently.

    history=2 (default), no TTL, FILE storage, replicas=1.
    Returns the bound KeyValue handle.
    """
    cfg = KeyValueConfig(
        bucket=KV_BUCKET,
        ttl=0,  # no TTL — persistent bot settings
        storage=StorageType.FILE,
        replicas=1,
    )
    try:
        kv = await js.create_key_value(cfg)
        log.info("bot_settings_kv: KV bucket %s created", KV_BUCKET)
        return kv
    except BadRequestError:
        log.debug("bot_settings_kv: KV bucket %s already exists, binding", KV_BUCKET)
        return await js.key_value(KV_BUCKET)
    except Exception:
        log.exception("bot_settings_kv: KV bucket %s provision failed", KV_BUCKET)
        raise


async def get_watch_channels(kv: KeyValue, bot_id: str) -> frozenset[int]:
    """Return current watch_channels for *bot_id*, or empty frozenset."""
    try:
        entry = await kv.get(_watch_channels_key(bot_id))
    except KeyNotFoundError:
        return frozenset()
    return _parse_watch_channels(entry.value if entry else None)


async def watch_watch_channels(
    kv: KeyValue, bot_id: str
) -> AsyncGenerator[frozenset[int], None]:
    """Async generator yielding frozenset[int] updates for *bot_id*.

    First yield is the current value (if any). Subsequent yields fire on every
    KV put/delete for this key. None markers from the NATS watcher are
    skipped so the caller only sees actual value changes.

    Usage:
        async for channels in watch_watch_channels(kv, bot_id):
            adapter._watch_channels = channels
    """
    watcher = await kv.watch(_watch_channels_key(bot_id))
    try:
        async for entry in watcher:
            if entry is None:
                continue
            yield _parse_watch_channels(entry.value)
    finally:
        await watcher.stop()


async def put_watch_channels(
    kv: KeyValue, bot_id: str, channels: frozenset[int]
) -> None:
    """Write watch_channels for *bot_id* to KV.

    Stored as JSON list of integers.
    """
    raw = json.dumps(sorted(channels)).encode()
    await kv.put(_watch_channels_key(bot_id), raw)
    log.debug("bot_settings_kv: watch_channels written for bot_id=%s", bot_id)
