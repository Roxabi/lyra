"""Thread ownership tracking for DiscordAdapter."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.core.stores.thread_store_protocol import ThreadStoreProtocol

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
    except (sqlite3.Error, OSError, RuntimeError):
        log.exception(
            "ThreadStore: failed to persist claim for thread_id=%s", thread_id
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
