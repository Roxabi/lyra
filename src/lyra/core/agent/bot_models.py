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
# Conservative: operators opt-in to convenience values via TOML.
# "blocked" = fail-safe; auto_thread=False = opt-in; 24h = conservative.
DEFAULT_TRUST: str = "blocked"
DEFAULT_AUTO_THREAD: bool = False
DEFAULT_THREAD_HOT_HOURS: int = 24

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
    trusted_roles: list[str] = field(default_factory=list)
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
        if len(row) != 11:
            raise ValueError(f"Expected 11 columns, got {len(row)}")
        (
            platform,
            bot_id,
            agent,
            webhook_enabled,
            default_trust,
            owner_users_json,
            trusted_users_json,
            trusted_roles_json,
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

        # Parse trusted_roles — same guard.
        try:
            parsed_roles = json.loads(trusted_roles_json or "[]")
            if not isinstance(parsed_roles, list) or not all(
                isinstance(el, str) for el in parsed_roles
            ):
                log.warning("trusted_roles_json has unexpected shape; resetting to []")
                parsed_roles = []
        except (json.JSONDecodeError, TypeError):
            log.warning("trusted_roles_json is not valid JSON; resetting to []")
            parsed_roles = []

        # Validate default_trust before construction — __post_init__ raises on
        # invalid values, so legacy DB rows must be coerced here (not raised).
        coerced_trust = default_trust if default_trust in _VALID_TRUST_LEVELS else None
        if coerced_trust is None:
            log.warning(
                "BotRow.from_db_row: invalid default_trust=%r for (%s,%s),"
                " defaulting to %s",
                default_trust,
                platform,
                bot_id,
                DEFAULT_TRUST,
            )
            coerced_trust = DEFAULT_TRUST

        # Explicit None check for thread_hot_hours — `or` treats 0 as falsy,
        # which would incorrectly map a stored 0 → DEFAULT_THREAD_HOT_HOURS.
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
            default_trust=coerced_trust,
            owner_users=parsed_owner,
            trusted_users=parsed_trusted,
            trusted_roles=parsed_roles,
            auto_thread=bool(auto_thread),
            thread_hot_hours=coerced_hours,
            updated_at=updated_at,
        )
