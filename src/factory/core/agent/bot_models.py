"""Pure data models for bot configuration rows."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from factory.core.agent.agent_models import _utc_now_iso
from factory.core.agent.schema.bot_schema import _N_BOT_COLS

log = logging.getLogger(__name__)

__all__ = [
    "BotRow",
    "DEFAULT_AUTO_THREAD",
    "DEFAULT_THREAD_HOT_HOURS",
]

DEFAULT_AUTO_THREAD: bool = False
DEFAULT_THREAD_HOT_HOURS: int = 24  # const-ok: thread-hot window default, single SSoT


@dataclass
class BotRow:
    """One row from the bots table (adapter/runtime config only — no auth fields)."""

    platform: str
    bot_id: str
    agent: str
    webhook_enabled: bool = False
    auto_thread: bool = DEFAULT_AUTO_THREAD
    thread_hot_hours: int = DEFAULT_THREAD_HOT_HOURS
    updated_at: str = field(default_factory=_utc_now_iso)
    public_bot: str | None = None

    @classmethod
    def from_db_row(cls, row: tuple[Any, ...]) -> "BotRow":
        """Construct a BotRow from a raw aiosqlite SELECT tuple."""
        if len(row) != _N_BOT_COLS:
            raise ValueError(f"Expected {_N_BOT_COLS} columns, got {len(row)}")
        (
            platform,
            bot_id,
            agent,
            webhook_enabled,
            auto_thread,
            thread_hot_hours,
            updated_at,
            public_bot,
        ) = row

        coerced_hours = (
            thread_hot_hours
            if thread_hot_hours is not None
            else DEFAULT_THREAD_HOT_HOURS
        )

        return cls(
            platform=platform,
            bot_id=bot_id,
            agent=agent,
            webhook_enabled=bool(webhook_enabled),
            auto_thread=bool(auto_thread),
            thread_hot_hours=coerced_hours,
            updated_at=updated_at,
            public_bot=public_bot,
        )