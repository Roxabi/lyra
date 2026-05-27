"""Pure data models for bot configuration rows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from lyra.core.agent.agent_models import _utc_now_iso

log = logging.getLogger(__name__)

__all__ = [
    "BotRow",
    "DEFAULT_TRUST",
    "DEFAULT_AUTO_THREAD",
    "DEFAULT_THREAD_HOT_HOURS",
]

# Canonical defaults — single SSoT for BotRow, DDL, and _merge_bots seeder.
# "blocked" is the operator-safe default (requires explicit grant to interact).
DEFAULT_TRUST: str = "blocked"
DEFAULT_AUTO_THREAD: bool = True
DEFAULT_THREAD_HOT_HOURS: int = 36

_VALID_TRUST_LEVELS = {"owner", "trusted", "public", "blocked"}


@dataclass
class BotRow:
    """One row from the bots table."""

    platform: str
    bot_id: str
    agent: str
    webhook_enabled: bool = False
    default_trust: str = DEFAULT_TRUST
    owner_users: list[str] = field(default_factory=list)
    trusted_users: list[str] = field(default_factory=list)
    auto_thread: bool = DEFAULT_AUTO_THREAD
    thread_hot_hours: int = DEFAULT_THREAD_HOT_HOURS
    updated_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.default_trust not in _VALID_TRUST_LEVELS:
            raise ValueError(
                f"Invalid default_trust {self.default_trust!r}; "
                f"must be one of {sorted(_VALID_TRUST_LEVELS)}"
            )

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

        # Parse owner_users — guard against malformed JSON or wrong shape.
        try:
            parsed_owner = json.loads(owner_users_json or "[]")
            if not isinstance(parsed_owner, list) or not all(
                isinstance(el, str) for el in parsed_owner
            ):
                log.warning("owner_users_json has unexpected shape; resetting to []")
                parsed_owner = []
        except (json.JSONDecodeError, TypeError):
            log.warning("owner_users_json is not valid JSON; resetting to []")
            parsed_owner = []

        # Parse trusted_users — same guard.
        try:
            parsed_trusted = json.loads(trusted_users_json or "[]")
            if not isinstance(parsed_trusted, list) or not all(
                isinstance(el, str) for el in parsed_trusted
            ):
                log.warning("trusted_users_json has unexpected shape; resetting to []")
                parsed_trusted = []
        except (json.JSONDecodeError, TypeError):
            log.warning("trusted_users_json is not valid JSON; resetting to []")
            parsed_trusted = []

        return cls(
            platform=platform,
            bot_id=bot_id,
            agent=agent,
            webhook_enabled=bool(webhook_enabled),
            default_trust=default_trust or DEFAULT_TRUST,
            owner_users=parsed_owner,
            trusted_users=parsed_trusted,
            auto_thread=bool(auto_thread),
            thread_hot_hours=thread_hot_hours or DEFAULT_THREAD_HOT_HOURS,
            updated_at=updated_at,
        )
