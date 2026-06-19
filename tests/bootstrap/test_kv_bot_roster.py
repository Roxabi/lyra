"""Unit tests for factory.bootstrap.wiring.kv_bot_roster."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import BucketNotFoundError, KeyNotFoundError

from factory.bootstrap.wiring.kv_bot_roster import (
    publish_bot_roster,
    seed_bot_roster,
)
from factory.config import (
    DISCORD_DEFAULT_AUTO_THREAD,
    DISCORD_DEFAULT_THREAD_HOT_HOURS,
    DiscordMultiConfig,
    TelegramMultiConfig,
)
from roxabi_contracts.state.bot_roster import (
    PlatformRosterDocument,
    RosterBotEntry,
    roster_key,
)
from tests.helpers.bot_store import make_bot_row


def _mock_kv(
    *, get_entry: object | None = None, puts: dict[str, bytes] | None = None
) -> MagicMock:
    kv = MagicMock()
    store = puts if puts is not None else {}

    async def _put(key: str, value: bytes) -> None:
        store[key] = value

    async def _get(key: str) -> MagicMock:
        if get_entry is None:
            raise KeyNotFoundError
        if isinstance(get_entry, Exception):
            raise get_entry
        entry = MagicMock()
        entry.value = get_entry
        return entry

    kv.put = AsyncMock(side_effect=_put)
    kv.get = AsyncMock(side_effect=_get)
    kv._store = store
    return kv


def _mock_js(*, kv: MagicMock | None = None) -> MagicMock:
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv or _mock_kv())
    js.create_key_value = AsyncMock(return_value=kv or _mock_kv())
    return js


@pytest.mark.asyncio
async def test_publish_bot_roster_writes_platform_keys() -> None:
    kv = _mock_kv()
    js = _mock_js(kv=kv)
    bot_store = MagicMock()
    bot_store.get_all.return_value = [
        make_bot_row(platform="telegram", bot_id="lyra"),
        make_bot_row(platform="discord", bot_id="aryl", auto_thread=True),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "factory.bootstrap.wiring.kv_bot_roster._open_or_create_kv",
            AsyncMock(return_value=kv),
        )
        await publish_bot_roster(js, bot_store)

    assert roster_key("telegram") in kv._store
    assert roster_key("discord") in kv._store

    tg_doc = PlatformRosterDocument.model_validate_json(
        kv._store[roster_key("telegram")]
    )
    dc_doc = PlatformRosterDocument.model_validate_json(
        kv._store[roster_key("discord")]
    )
    assert [b.bot_id for b in tg_doc.bots] == ["lyra"]
    assert [b.bot_id for b in dc_doc.bots] == ["aryl"]
    assert "owner_users" not in json.loads(kv._store[roster_key("telegram")])


@pytest.mark.asyncio
async def test_publish_bot_roster_never_includes_auth_fields() -> None:
    kv = _mock_kv()
    js = _mock_js(kv=kv)
    row = make_bot_row(
        platform="telegram",
        bot_id="lyra",
        owner_users=["tg:user:1"],
        trusted_users=["tg:user:2"],
    )
    bot_store = MagicMock()
    bot_store.get_all.return_value = [row]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "factory.bootstrap.wiring.kv_bot_roster._open_or_create_kv",
            AsyncMock(return_value=kv),
        )
        await publish_bot_roster(js, bot_store)

    raw = json.loads(kv._store[roster_key("telegram")])
    assert raw["bots"][0].keys() <= {"bot_id", "agent", "webhook_enabled"}


@pytest.mark.asyncio
async def test_seed_bot_roster_telegram_returns_config() -> None:
    doc = PlatformRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        bots=[
            RosterBotEntry(
                bot_id="lyra",
                agent="lyra_default",
                webhook_enabled=False,
            )
        ],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    result = await seed_bot_roster(js, "telegram")

    assert isinstance(result, TelegramMultiConfig)
    assert result.bots[0].bot_id == "lyra"


@pytest.mark.asyncio
async def test_seed_bot_roster_discord_applies_defaults() -> None:
    doc = PlatformRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        bots=[RosterBotEntry(bot_id="aryl", agent="lyra_default")],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    result = await seed_bot_roster(js, "discord")

    assert isinstance(result, DiscordMultiConfig)
    assert result.bots[0].bot_id == "aryl"
    assert result.bots[0].auto_thread is DISCORD_DEFAULT_AUTO_THREAD
    assert result.bots[0].thread_hot_hours == DISCORD_DEFAULT_THREAD_HOT_HOURS


@pytest.mark.asyncio
async def test_seed_bot_roster_missing_key_exits() -> None:
    kv = _mock_kv(get_entry=KeyNotFoundError())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="roster missing or invalid"):
        await seed_bot_roster(js, "telegram")


@pytest.mark.asyncio
async def test_seed_bot_roster_empty_bots_exits() -> None:
    doc = PlatformRosterDocument(updated_at="2026-06-19T12:00:00Z", bots=[])
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="empty bots"):
        await seed_bot_roster(js, "discord")


@pytest.mark.asyncio
async def test_seed_bot_roster_bucket_missing_exits() -> None:
    js = MagicMock()
    js.key_value = AsyncMock(side_effect=BucketNotFoundError)

    with pytest.raises(SystemExit, match="bucket not found"):
        await seed_bot_roster(js, "telegram")


@pytest.mark.asyncio
async def test_seed_bot_roster_rejects_auth_fields_in_kv() -> None:
    raw = json.dumps(
        {
            "schema_version": 1,
            "updated_at": "2026-06-19T12:00:00Z",
            "bots": [
                {
                    "bot_id": "lyra",
                    "agent": "lyra_default",
                    "owner_users": ["tg:user:1"],
                }
            ],
        }
    ).encode()
    kv = _mock_kv(get_entry=raw)
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="roster missing or invalid"):
        await seed_bot_roster(js, "telegram")


@pytest.mark.asyncio
async def test_seed_bot_roster_malformed_json_exits() -> None:
    kv = _mock_kv(get_entry=b"not-json{{{")
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="malformed JSON"):
        await seed_bot_roster(js, "telegram")


@pytest.mark.asyncio
async def test_seed_bot_roster_timeout_exits() -> None:
    kv = MagicMock()

    async def _slow_get(_key: str) -> MagicMock:
        await asyncio.sleep(1.0)  # event-based
        entry = MagicMock()
        entry.value = b"{}"
        return entry

    kv.get = AsyncMock(side_effect=_slow_get)
    js = MagicMock()
    js.key_value = AsyncMock(return_value=kv)

    with pytest.raises(SystemExit, match="timeout"):
        await seed_bot_roster(js, "telegram", timeout=0.05)


@pytest.mark.asyncio
async def test_seed_bot_roster_rejects_invalid_bot_id() -> None:
    doc = PlatformRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        bots=[RosterBotEntry(bot_id="../../evil", agent="lyra_default")],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    with pytest.raises(SystemExit, match="invalid bot_id"):
        await seed_bot_roster(js, "telegram")


@pytest.mark.asyncio
async def test_seed_bot_roster_discord_two_bots() -> None:
    doc = PlatformRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        bots=[
            RosterBotEntry(bot_id="aryl", agent="lyra_default"),
            RosterBotEntry(
                bot_id="helper",
                agent="helper_agent",
                auto_thread=True,
                thread_hot_hours=6,
            ),
        ],
    )
    kv = _mock_kv(get_entry=doc.model_dump_json().encode())
    js = _mock_js(kv=kv)

    result = await seed_bot_roster(js, "discord")

    assert [b.bot_id for b in result.bots] == ["aryl", "helper"]
    assert result.bots[1].auto_thread is True
    assert result.bots[1].thread_hot_hours == 6


@pytest.mark.asyncio
async def test_publish_bot_roster_skips_invalid_bot_id() -> None:
    kv = _mock_kv()
    js = _mock_js(kv=kv)
    bot_store = MagicMock()
    bot_store.get_all.return_value = [
        make_bot_row(platform="telegram", bot_id="../../evil"),
        make_bot_row(platform="telegram", bot_id="lyra"),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "factory.bootstrap.wiring.kv_bot_roster._open_or_create_kv",
            AsyncMock(return_value=kv),
        )
        await publish_bot_roster(js, bot_store)

    tg_doc = PlatformRosterDocument.model_validate_json(
        kv._store[roster_key("telegram")]
    )
    assert [b.bot_id for b in tg_doc.bots] == ["lyra"]
