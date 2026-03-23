"""Thread ownership tracking and session persistence for DiscordAdapter."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage
    from lyra.core.stores.thread_store import ThreadStore

log = logging.getLogger(__name__)


async def persist_thread_claim(
    thread_store: "ThreadStore",
    thread_id: int,
    bot_id: str,
    channel_id: int,
    guild_id: int | None,
) -> None:
    """Persist thread ownership to ThreadStore (fire-and-forget)."""
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
    thread_store: "ThreadStore",
    msg: "InboundMessage",
    session_id: str,
    pool_id: str,
    bot_id: str,
    cache: dict[str, tuple[str, str]],
) -> None:
    """Persist session_id and pool_id for a thread after a successful turn."""
    thread_id: int | None = msg.platform_meta.get("thread_id")
    if thread_id is None:
        return
    try:
        await thread_store.update_session(
            thread_id=str(thread_id),
            bot_id=bot_id,
            session_id=session_id,
            pool_id=pool_id,
        )
        cache[str(thread_id)] = (session_id, pool_id)
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
    thread_store: "ThreadStore",
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
    thread_store: "ThreadStore",
    thread_id: str,
    bot_id: str,
    cache: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None]:
    """Look up session_id and pool_id for an owned thread.

    Checks the in-memory *cache* first; on a miss, queries ThreadStore and
    warms the cache on a hit.
    """
    cached = cache.get(thread_id)
    if cached is not None:
        return cached

    stored_session_id, stored_pool_id = await thread_store.get_session(
        thread_id=thread_id,
        bot_id=bot_id,
    )
    if stored_session_id is not None:
        log.debug(
            "ThreadStore: retrieved session_id=%s pool_id=%s for thread_id=%s",
            stored_session_id,
            stored_pool_id,
            thread_id,
        )
    return stored_session_id, stored_pool_id
