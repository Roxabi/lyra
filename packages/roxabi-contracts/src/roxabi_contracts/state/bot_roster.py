"""Bot roster documents for ``factory-state`` KV (``roster.<platform>`` keys).

Adapter-facing projection only — auth fields (``owner_users``, grants) must
never appear on the wire. ``extra='forbid'`` on entry models enforces this at
parse time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FACTORY_STATE_BUCKET = "factory-state"
_ROSTER_SCHEMA_VERSION: Literal[1] = 1

PlatformName = Literal["telegram", "discord"]


def roster_key(platform: PlatformName) -> str:
    """KV key for a platform roster index (``roster.telegram`` / ``roster.discord``)."""
    return f"roster.{platform}"


class RosterBotEntry(BaseModel):
    """One bot in a platform roster document (union of adapter-facing fields)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_id: str
    agent: str = "lyra_default"
    auto_thread: bool | None = None
    thread_hot_hours: int | None = None
    webhook_enabled: bool | None = None


class TelegramRosterBot(BaseModel):
    """Telegram bot entry for publish-time validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_id: str
    agent: str = "lyra_default"
    webhook_enabled: bool = False


class DiscordRosterBot(BaseModel):
    """Discord bot entry for publish-time validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_id: str
    agent: str = "lyra_default"
    auto_thread: bool | None = None
    thread_hot_hours: int | None = None


class PlatformRosterDocument(BaseModel):
    """JSON document stored at ``roster.<platform>``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = _ROSTER_SCHEMA_VERSION
    updated_at: str
    bots: list[RosterBotEntry] = Field(default_factory=list)
