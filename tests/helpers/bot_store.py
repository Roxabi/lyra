"""BotStore test helpers (plain functions, no pytest fixtures)."""

from __future__ import annotations

from pathlib import Path

from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.bot_store import BotStore


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
