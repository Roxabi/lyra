"""Thread ownership tracking and session persistence for DiscordAdapter."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from lyra.core.messaging.message import DiscordMeta
from lyra.core.stores.thread_store_protocol import ThreadSession

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.core.stores.thread_store_protocol import ThreadStoreProtocol

log = logging.getLogger(__name__)


async def persist_thread_claim(
    thread_store: "ThreadStoreProtocol",
    thread_id: int,
    bot_id: str,
    channel_id: int,
    guild_id: int | None,
) -> None:
    """Persist thread ownership to ThreadStore. Awaitable — callers must await."""
    try:
        await thread_store.claim(
            thread_id=str(thread_id),
            bot_id=bot_id,
            channel_id=str(channel_id),
            guild_id=str(guild_id) if guild_id is not None else None,
        )
        log.debug(
            "ThreadStore: claimed thread_id=%s for bot_id=%r",
            thread_id,
            bot_id,
        )
    except Exception:
        log.exception(
            "ThreadStore: failed to persist claim for thread_id=%s", thread_id
        )


async def persist_thread_session(  # noqa: PLR0913 — each arg is a distinct required dependency
    thread_store: "ThreadStoreProtocol",
    msg: "InboundMessage",
    session_id: str,
    pool_id: str,
    bot_id: str,
    cache: dict[str, ThreadSession],
) -> None:
    """Persist session_id and pool_id for a thread after a successful turn."""
    if not isinstance(msg.platform_meta, DiscordMeta):
        return
    thread_id: int | None = msg.platform_meta.thread_id
    if thread_id is None:
        return
    try:
        await thread_store.update_session(
            thread_id=str(thread_id),
            bot_id=bot_id,
            session_id=session_id,
            pool_id=pool_id,
        )
        if len(cache) >= 500:
            oldest_key = next(iter(cache))
            del cache[oldest_key]
            log.debug(
                "ThreadStore: evicted oldest thread_sessions"
                " entry thread_id=%s (cache full)",
                oldest_key,
            )
        _key = str(thread_id)
        cache.pop(_key, None)
        cache[_key] = ThreadSession(session_id=session_id, pool_id=pool_id)
        log.debug(
            "ThreadStore: persisted session_id=%s pool_id=%s for thread_id=%s",
            session_id,
            pool_id,
            thread_id,
        )
    except Exception:
        log.exception(
            "ThreadStore: failed to persist session for thread_id=%s", thread_id
        )


async def restore_hot_threads(
    thread_store: "ThreadStoreProtocol",
    bot_id: str,
    hot_hours: int,
) -> set[int]:
    """Load recently active owned thread IDs from ThreadStore.

    Only threads updated within *hot_hours* are returned; older threads are
    handled by a lazy DB lookup in on_message so the in-memory set doesn't
    grow unboundedly over time.
    """
    hot_since = datetime.now(UTC) - timedelta(hours=hot_hours)
    thread_ids = await thread_store.get_thread_ids(bot_id, active_since=hot_since)
    owned = {int(tid) for tid in thread_ids}
    log.info(
        "ThreadStore: restored %d hot thread(s) (< %d h) for bot_id=%r",
        len(owned),
        hot_hours,
        bot_id,
    )
    return owned


async def retrieve_thread_session(
    thread_store: "ThreadStoreProtocol",
    thread_id: str,
    bot_id: str,
    cache: dict[str, ThreadSession],
) -> ThreadSession:
    """Look up session_id and pool_id for an owned thread.

    Checks the in-memory *cache* first; on a miss, queries ThreadStore and
    warms the cache on a hit.
    """
    cached = cache.get(thread_id)
    if cached is not None:
        cache[thread_id] = cache.pop(thread_id)
        return cached

    ts = await thread_store.get_session(thread_id=thread_id, bot_id=bot_id)
    if ts.session_id is not None and ts.pool_id is not None:
        if len(cache) >= 500:
            _oldest = next(iter(cache))
            del cache[_oldest]
        cache[thread_id] = ts
        log.debug(
            "ThreadStore: retrieved session_id=%s pool_id=%s for thread_id=%s",
            ts.session_id,
            ts.pool_id,
            thread_id,
        )
    return ts
