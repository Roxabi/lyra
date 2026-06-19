"""Tests for BotStore-backed roster loading in standalone adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.bootstrap.wiring._standalone_bot_store import (
    load_discord_roster_from_store,
    load_telegram_roster_from_store,
)
from factory.infrastructure.stores.bot_store import BotStore
from tests.helpers.bot_store import make_bot_row


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

    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))

    dc = await load_discord_roster_from_store()

    assert len(dc.bots) == 1
    assert dc.bots[0].bot_id == "aryl"
    assert dc.bots[0].auto_thread is True
    assert dc.bots[0].thread_hot_hours == 12