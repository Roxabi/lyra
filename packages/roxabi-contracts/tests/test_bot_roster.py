"""Tests for factory-state bot roster wire contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from roxabi_contracts.state.bot_roster import (
    DiscordRosterBot,
    PlatformRosterDocument,
    RosterBotEntry,
    TelegramRosterBot,
    roster_key,
)


def test_roster_key_platform_index() -> None:
    assert roster_key("telegram") == "roster.telegram"
    assert roster_key("discord") == "roster.discord"


def test_platform_roster_round_trip_json() -> None:
    doc = PlatformRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        bots=[
            RosterBotEntry(bot_id="lyra", agent="lyra_default", webhook_enabled=False),
            RosterBotEntry(
                bot_id="aryl",
                agent="lyra_default",
                auto_thread=True,
                thread_hot_hours=36,
            ),
        ],
    )
    raw = doc.model_dump_json()
    restored = PlatformRosterDocument.model_validate_json(raw)
    assert restored.schema_version == 1
    assert len(restored.bots) == 2
    assert restored.bots[0].bot_id == "lyra"


def test_roster_entry_rejects_auth_fields() -> None:
    with pytest.raises(ValidationError):
        RosterBotEntry.model_validate(
            {
                "bot_id": "lyra",
                "owner_users": ["tg:user:1"],
            }
        )


def test_telegram_roster_bot_rejects_trusted_users() -> None:
    with pytest.raises(ValidationError):
        TelegramRosterBot.model_validate(
            {
                "bot_id": "lyra",
                "trusted_users": ["dc:user:1"],
            }
        )


def test_discord_roster_bot_rejects_default_trust() -> None:
    with pytest.raises(ValidationError):
        DiscordRosterBot.model_validate(
            {
                "bot_id": "lyra",
                "default_trust": "owner",
            }
        )


def test_platform_document_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValidationError):
        PlatformRosterDocument.model_validate(
            {
                "schema_version": 1,
                "updated_at": "2026-06-19T12:00:00Z",
                "bots": [],
                "owner_users": [],
            }
        )


def test_roster_entry_accepts_optional_public_bot() -> None:
    entry = RosterBotEntry(
        bot_id="lyra",
        agent="lyra_default",
        public_bot="@lyra_public",
    )
    assert entry.public_bot == "@lyra_public"


def test_golden_telegram_shape() -> None:
    payload = {
        "schema_version": 1,
        "updated_at": "2026-06-19T12:00:00Z",
        "bots": [
            {
                "bot_id": "lyra",
                "agent": "lyra_default",
                "webhook_enabled": False,
            }
        ],
    }
    doc = PlatformRosterDocument.model_validate(payload)
    assert json.loads(doc.model_dump_json(exclude_none=True)) == payload
