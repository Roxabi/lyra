"""Pure data models for bot configuration rows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from lyra.core.agent.agent_models import _utc_now_iso

__all__ = ["BotRow"]


@dataclass
class BotRow:
    """One row from the bots table."""

    platform: str
    bot_id: str
    agent: str
    webhook_enabled: bool = False
    default_trust: str = "untrusted"
    owner_users: list[str] = field(default_factory=list)
    trusted_users: list[str] = field(default_factory=list)
    auto_thread: bool = False
    thread_hot_hours: int = 24
    updated_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_db_row(cls, row: tuple[Any, ...]) -> "BotRow":
        """Construct a BotRow from a raw aiosqlite SELECT tuple."""
        if len(row) != 10:
            raise ValueError(f"Expected 10 columns, got {len(row)}")
        (
            platform,
            bot_id,
            agent,
            webhook_enabled,
            default_trust,
            owner_users_json,
            trusted_users_json,
            auto_thread,
            thread_hot_hours,
            updated_at,
        ) = row
        return cls(
            platform=platform,
            bot_id=bot_id,
            agent=agent,
            webhook_enabled=bool(webhook_enabled),
            default_trust=default_trust or "untrusted",
            owner_users=json.loads(owner_users_json or "[]"),
            trusted_users=json.loads(trusted_users_json or "[]"),
            auto_thread=bool(auto_thread),
            thread_hot_hours=thread_hot_hours or 24,
            updated_at=updated_at,
        )
