"""Shared SQLite store connection helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path

from factory.infrastructure.stores.registry.agent_store import AgentStore
from factory.infrastructure.stores.registry.bot_store import BotStore
from factory.paths import factory_data_dir


def _get_db_path() -> Path:
    return factory_data_dir() / "config.db"


async def _connect_store() -> AgentStore:
    store = AgentStore(db_path=_get_db_path())
    await store.connect()
    return store


async def _connect_bot_store() -> BotStore:
    store = BotStore(db_path=_get_db_path())
    await store.connect()
    return store
