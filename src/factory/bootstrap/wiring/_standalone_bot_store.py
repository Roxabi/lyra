"""BotStore-backed roster loading for standalone platform adapters."""

from __future__ import annotations

import logging
import sys

from factory.config import (
    DiscordMultiConfig,
    TelegramMultiConfig,
    multibot_config_from_store,
)
from factory.infrastructure.stores.bot_store import BotStore
from factory.paths import factory_data_dir

log = logging.getLogger(__name__)


async def _open_bot_store() -> BotStore:
    config_path = factory_data_dir() / "config.db"
    if not config_path.exists():
        sys.exit(
            "config.db not found — runtime roster is sourced from BotStore."
            " Run 'factory bot init' on the hub host to seed it."
        )
    store = BotStore(db_path=config_path)
    await store.connect_readonly()
    return store


async def load_telegram_roster_from_store() -> TelegramMultiConfig:
    """Load the Telegram bot roster from BotStore (runtime SSoT)."""
    store = await _open_bot_store()
    try:
        tg, _ = multibot_config_from_store(store)
        log.info("Loaded %d telegram bot(s) from BotStore", len(tg.bots))
        return tg
    finally:
        await store.close()


async def load_discord_roster_from_store() -> DiscordMultiConfig:
    """Load the Discord bot roster from BotStore (runtime SSoT)."""
    store = await _open_bot_store()
    try:
        _, dc = multibot_config_from_store(store)
        log.info("Loaded %d discord bot(s) from BotStore", len(dc.bots))
        return dc
    finally:
        await store.close()