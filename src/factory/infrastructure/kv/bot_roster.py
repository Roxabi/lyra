"""Publish bot roster documents into factory-state KV (hub / CLI dual-write)."""

from __future__ import annotations

import logging
import re

from factory.core.agent.agent_models import _utc_now_iso
from factory.core.agent.bot_models import BotRow
from roxabi_contracts.state.bot_roster import (
    DiscordRosterBot,
    PlatformRosterDocument,
    RosterBotEntry,
    TelegramRosterBot,
    roster_key,
)

from .factory_state import open_or_create_kv

log = logging.getLogger(__name__)

_BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _telegram_entry(row: BotRow) -> RosterBotEntry | None:
    if not _BOT_ID_RE.fullmatch(row.bot_id):
        log.warning(
            "publish_bot_roster: skipping telegram bot with invalid bot_id %r",
            row.bot_id,
        )
        return None
    validated = TelegramRosterBot(
        bot_id=row.bot_id,
        agent=row.agent,
        public_bot=row.public_bot,
        webhook_enabled=row.webhook_enabled,
    )
    return RosterBotEntry(
        bot_id=validated.bot_id,
        agent=validated.agent,
        public_bot=validated.public_bot,
        webhook_enabled=validated.webhook_enabled,
    )


def _discord_entry(row: BotRow) -> RosterBotEntry | None:
    if not _BOT_ID_RE.fullmatch(row.bot_id):
        log.warning(
            "publish_bot_roster: skipping discord bot with invalid bot_id %r",
            row.bot_id,
        )
        return None
    validated = DiscordRosterBot(
        bot_id=row.bot_id,
        agent=row.agent,
        public_bot=row.public_bot,
        auto_thread=row.auto_thread,
        thread_hot_hours=row.thread_hot_hours,
    )
    return RosterBotEntry(
        bot_id=validated.bot_id,
        agent=validated.agent,
        public_bot=validated.public_bot,
        auto_thread=validated.auto_thread,
        thread_hot_hours=validated.thread_hot_hours,
    )


def _build_documents(
    rows: list[BotRow],
) -> tuple[PlatformRosterDocument, PlatformRosterDocument]:
    updated_at = _utc_now_iso()
    tg_bots = [
        entry
        for row in rows
        if row.platform == "telegram"
        for entry in (_telegram_entry(row),)
        if entry is not None
    ]
    dc_bots = [
        entry
        for row in rows
        if row.platform == "discord"
        for entry in (_discord_entry(row),)
        if entry is not None
    ]
    return (
        PlatformRosterDocument(updated_at=updated_at, bots=tg_bots),
        PlatformRosterDocument(updated_at=updated_at, bots=dc_bots),
    )


async def publish_bot_roster(js: object, bot_store: object) -> None:
    """Publish platform roster documents from BotStore into factory-state KV."""
    tg_doc, dc_doc = _build_documents(bot_store.get_all())  # type: ignore[attr-defined]
    kv = await open_or_create_kv(js)
    for platform, doc in (("telegram", tg_doc), ("discord", dc_doc)):
        key = roster_key(platform)  # type: ignore[arg-type]
        await kv.put(key, doc.model_dump_json(exclude_none=True).encode())
        log.debug(
            "publish_bot_roster: wrote %d bot(s) to %s",
            len(doc.bots),
            key,
        )
