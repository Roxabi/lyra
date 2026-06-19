"""Tests for BotStore-backed roster loading in standalone adapters."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from factory.bootstrap.wiring._standalone_bot_store import (
    load_discord_roster_from_store,
    load_telegram_roster_from_store,
)
from factory.infrastructure.stores.bot_store import BotStore
from tests.helpers.bot_store import make_bot_row


def _mark_config_db_complete(db_path: Path) -> None:
    """Match production config.db: migration sentinel prevents _ensure_config_db wipe."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _migration_complete (migrated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO _migration_complete (migrated_at) VALUES (datetime('now'))"
        )
        conn.commit()


@pytest.mark.bot_store_live
@pytest.mark.asyncio
async def test_load_telegram_roster_from_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BotStore(db_path=tmp_path / "config.db")
    await store.connect()
    await store.upsert(make_bot_row(platform="telegram", bot_id="lyra"))
    await store.upsert(make_bot_row(platform="discord", bot_id="lyra"))
    await store.close()
    _mark_config_db_complete(tmp_path / "config.db")

    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))

    tg = await load_telegram_roster_from_store()

    assert [b.bot_id for b in tg.bots] == ["lyra"]


@pytest.mark.bot_store_live
@pytest.mark.asyncio
async def test_load_discord_roster_from_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BotStore(db_path=tmp_path / "config.db")
    await store.connect()
    await store.upsert(
        make_bot_row(
            platform="discord",
            bot_id="aryl",
            auto_thread=True,
            thread_hot_hours=12,
        )
    )
    await store.close()
    _mark_config_db_complete(tmp_path / "config.db")

    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))

    dc = await load_discord_roster_from_store()

    assert len(dc.bots) == 1
    assert dc.bots[0].bot_id == "aryl"
    assert dc.bots[0].auto_thread is True
    assert dc.bots[0].thread_hot_hours == 12