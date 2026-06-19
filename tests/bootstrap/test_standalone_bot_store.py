"""Tests for KV-backed roster loading in standalone adapters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.wiring.kv_bot_roster import seed_bot_roster
from factory.infrastructure.kv.bot_roster import publish_bot_roster
from factory.config import DiscordMultiConfig, TelegramMultiConfig
from factory.infrastructure.stores.bot_store import BotStore
from roxabi_contracts.state.bot_roster import roster_key
from tests.helpers.bot_store import make_bot_row


@pytest.mark.bot_store_live
@pytest.mark.asyncio
async def test_publish_then_seed_telegram_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BotStore(db_path=tmp_path / "config.db")
    await store.connect()
    await store.upsert(make_bot_row(platform="telegram", bot_id="lyra"))
    await store.upsert(make_bot_row(platform="discord", bot_id="lyra"))
    puts: dict[str, bytes] = {}
    kv = MagicMock()

    async def _put(key: str, value: bytes) -> None:
        puts[key] = value

    kv.put = AsyncMock(side_effect=_put)
    js = MagicMock()

    monkeypatch.setattr(
        "factory.infrastructure.kv.bot_roster.open_or_create_kv",
        AsyncMock(return_value=kv),
    )
    await publish_bot_roster(js, store)
    await store.close()

    entry = MagicMock()
    entry.value = puts[roster_key("telegram")]
    kv.get = AsyncMock(return_value=entry)
    js.key_value = AsyncMock(return_value=kv)

    tg = await seed_bot_roster(js, "telegram")

    assert isinstance(tg, TelegramMultiConfig)
    assert [b.bot_id for b in tg.bots] == ["lyra"]


@pytest.mark.bot_store_live
@pytest.mark.asyncio
async def test_publish_then_seed_discord_roster(
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
    puts: dict[str, bytes] = {}
    kv = MagicMock()

    async def _put(key: str, value: bytes) -> None:
        puts[key] = value

    kv.put = AsyncMock(side_effect=_put)
    js = MagicMock()

    monkeypatch.setattr(
        "factory.infrastructure.kv.bot_roster.open_or_create_kv",
        AsyncMock(return_value=kv),
    )
    await publish_bot_roster(js, store)
    await store.close()

    entry = MagicMock()
    entry.value = puts[roster_key("discord")]
    kv.get = AsyncMock(return_value=entry)
    js.key_value = AsyncMock(return_value=kv)

    dc = await seed_bot_roster(js, "discord")

    assert isinstance(dc, DiscordMultiConfig)
    assert dc.bots[0].bot_id == "aryl"
    assert dc.bots[0].auto_thread is True
    assert dc.bots[0].thread_hot_hours == 12