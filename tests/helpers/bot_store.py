"""BotStore test helpers (plain functions, no pytest fixtures)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from factory.core.agent.bot_models import BotRow
from factory.infrastructure.stores.bot_store import BotStore


def make_bot_row(
    platform: str = "telegram",
    bot_id: str = "main",
    agent: str = "lyra_default",
    **overrides,
) -> BotRow:
    """Return a minimal valid BotRow for the given key."""
    return BotRow(
        platform=platform,
        bot_id=bot_id,
        agent=agent,
        **overrides,
    )


async def make_bot_store(tmp_path: Path) -> BotStore:
    """Create and connect a real BotStore backed by a tmp file DB."""
    store = BotStore(db_path=str(tmp_path / "bots.db"))
    await store.connect()
    return store


def db_get(db_path: Path, platform: str, bot_id: str) -> BotRow | None:
    """Read a bot row from DB synchronously (for use in CLI tests)."""

    async def _run() -> BotRow | None:
        store = BotStore(db_path=str(db_path))
        await store.connect()
        row = store.get(platform, bot_id)
        await store.close()
        return row

    return asyncio.run(_run())


def db_upsert(db_path: Path, row: BotRow) -> None:
    """Upsert a bot row into DB synchronously (for use in CLI tests)."""

    async def _run() -> None:
        store = BotStore(db_path=str(db_path))
        await store.connect()
        await store.upsert(row)
        await store.close()

    asyncio.run(_run())
