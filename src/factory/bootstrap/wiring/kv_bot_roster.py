"""Hub-publish / adapter-read bot roster in ``factory-state`` KV.

Keys: ``roster.telegram``, ``roster.discord`` — platform index documents.
BotStore remains the write SSoT on the hub host; KV is the adapter read mirror.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Literal, overload

from factory.config import (
    DISCORD_DEFAULT_AUTO_THREAD,
    DISCORD_DEFAULT_THREAD_HOT_HOURS,
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from factory.core.agent.agent_models import _utc_now_iso
from factory.core.agent.bot_models import BotRow
from roxabi_contracts.state.bot_roster import (
    DiscordRosterBot,
    PlatformName,
    PlatformRosterDocument,
    RosterBotEntry,
    TelegramRosterBot,
    roster_key,
)

from .kv_watch_channels import _open_or_create_kv

log = logging.getLogger(__name__)

_BUCKET = "factory-state"


def _telegram_entry(row: BotRow) -> RosterBotEntry:
    TelegramRosterBot(
        bot_id=row.bot_id,
        agent=row.agent,
        webhook_enabled=row.webhook_enabled,
    )
    return RosterBotEntry(
        bot_id=row.bot_id,
        agent=row.agent,
        webhook_enabled=row.webhook_enabled,
    )


def _discord_entry(row: BotRow) -> RosterBotEntry:
    DiscordRosterBot(
        bot_id=row.bot_id,
        agent=row.agent,
        auto_thread=row.auto_thread,
        thread_hot_hours=row.thread_hot_hours,
    )
    return RosterBotEntry(
        bot_id=row.bot_id,
        agent=row.agent,
        auto_thread=row.auto_thread,
        thread_hot_hours=row.thread_hot_hours,
    )


def _build_documents(
    rows: list[BotRow],
) -> tuple[PlatformRosterDocument, PlatformRosterDocument]:
    updated_at = _utc_now_iso()
    tg_bots = [_telegram_entry(row) for row in rows if row.platform == "telegram"]
    dc_bots = [_discord_entry(row) for row in rows if row.platform == "discord"]
    return (
        PlatformRosterDocument(updated_at=updated_at, bots=tg_bots),
        PlatformRosterDocument(updated_at=updated_at, bots=dc_bots),
    )


def _entry_to_telegram_bot(entry: RosterBotEntry) -> TelegramBotConfig:
    return TelegramBotConfig(
        bot_id=entry.bot_id,
        agent=entry.agent,
    )


def _entry_to_discord_bot(entry: RosterBotEntry) -> DiscordBotConfig:
    return DiscordBotConfig(
        bot_id=entry.bot_id,
        agent=entry.agent,
        auto_thread=(
            entry.auto_thread
            if entry.auto_thread is not None
            else DISCORD_DEFAULT_AUTO_THREAD
        ),
        thread_hot_hours=(
            entry.thread_hot_hours
            if entry.thread_hot_hours is not None
            else DISCORD_DEFAULT_THREAD_HOT_HOURS
        ),
    )


def _fatal_roster_error(platform: str, detail: str) -> None:
    sys.exit(
        f"No {platform} bots configured — roster missing or invalid in factory-state KV"
        f" ({detail}). Ensure the hub has started and published roster.{platform},"
        " or run 'factory bot init' on the hub host."
    )


async def publish_bot_roster(js: object, bot_store: object) -> None:
    """Publish platform roster documents from BotStore into factory-state KV."""
    tg_doc, dc_doc = _build_documents(bot_store.get_all())  # type: ignore[attr-defined]
    kv = await _open_or_create_kv(js)
    for platform, doc in (("telegram", tg_doc), ("discord", dc_doc)):
        key = roster_key(platform)  # type: ignore[arg-type]
        await kv.put(key, doc.model_dump_json(exclude_none=True).encode())
        log.debug(
            "publish_bot_roster: wrote %d bot(s) to %s",
            len(doc.bots),
            key,
        )


@overload
async def seed_bot_roster(
    js: object,
    platform: Literal["telegram"],
    *,
    timeout: float = 2.0,
) -> TelegramMultiConfig: ...


@overload
async def seed_bot_roster(
    js: object,
    platform: Literal["discord"],
    *,
    timeout: float = 2.0,
) -> DiscordMultiConfig: ...


async def seed_bot_roster(
    js: object,
    platform: PlatformName,
    *,
    timeout: float = 2.0,
) -> TelegramMultiConfig | DiscordMultiConfig:
    """Read one platform roster from KV — fatal when absent, malformed, or empty."""
    from nats.js.errors import BucketNotFoundError, KeyNotFoundError

    key = roster_key(platform)
    try:
        async with asyncio.timeout(timeout):
            kv = await js.key_value(_BUCKET)  # type: ignore[attr-defined]
            entry = await kv.get(key)
    except BucketNotFoundError:
        _fatal_roster_error(platform, "bucket not found")
    except KeyNotFoundError:
        _fatal_roster_error(platform, f"key {key!r} not found")
    except TimeoutError:
        _fatal_roster_error(platform, f"timeout reading {key!r}")

    try:
        doc = PlatformRosterDocument.model_validate_json(entry.value)
    except (json.JSONDecodeError, TypeError, ValueError):
        _fatal_roster_error(platform, f"malformed JSON at {key!r}")

    if not doc.bots:
        _fatal_roster_error(platform, f"empty bots[] at {key!r}")

    if platform == "telegram":
        bots = [_entry_to_telegram_bot(entry) for entry in doc.bots]
        log.info("Loaded %d telegram bot(s) from KV", len(bots))
        return TelegramMultiConfig(bots=bots)

    bots = [_entry_to_discord_bot(entry) for entry in doc.bots]
    log.info("Loaded %d discord bot(s) from KV", len(bots))
    return DiscordMultiConfig(bots=bots)
