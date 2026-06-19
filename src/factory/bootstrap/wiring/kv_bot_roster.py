"""Adapter-read bot roster from ``factory-state`` KV.

Keys: ``roster.telegram``, ``roster.discord`` — platform index documents.
BotStore remains the write SSoT on the hub host; KV is the adapter read mirror.

Publish lives in :mod:`factory.infrastructure.kv.bot_roster` (import-layer safe).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from typing import Literal, overload

from pydantic import ValidationError

from factory.config import (
    DISCORD_DEFAULT_AUTO_THREAD,
    DISCORD_DEFAULT_THREAD_HOT_HOURS,
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from factory.infrastructure.kv.bot_roster import publish_bot_roster
from roxabi_contracts.state.bot_roster import (
    PlatformName,
    PlatformRosterDocument,
    RosterBotEntry,
    roster_key,
)

log = logging.getLogger(__name__)

_BUCKET = "factory-state"
_BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

__all__ = ["publish_bot_roster", "seed_bot_roster"]


def _validate_bot_id(bot_id: str, platform: str) -> None:
    if not _BOT_ID_RE.fullmatch(bot_id):
        _fatal_roster_error(platform, f"invalid bot_id {bot_id!r}")


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
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        _fatal_roster_error(platform, f"malformed JSON at {key!r}")

    if not doc.bots:
        _fatal_roster_error(platform, f"empty bots[] at {key!r}")

    for roster_entry in doc.bots:
        _validate_bot_id(roster_entry.bot_id, platform)

    if platform == "telegram":
        bots = [_entry_to_telegram_bot(entry) for entry in doc.bots]
        log.info("Loaded %d telegram bot(s) from KV", len(bots))
        return TelegramMultiConfig(bots=bots)

    bots = [_entry_to_discord_bot(entry) for entry in doc.bots]
    log.info("Loaded %d discord bot(s) from KV", len(bots))
    return DiscordMultiConfig(bots=bots)