"""Turns-domain NATS subject strings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["SUBJECTS"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for turns-domain subject literals."""

    turn_write: Literal["factory.turns.write"] = "factory.turns.write"
    # Core NATS request/reply (not JetStream) — hub → turn-writer reads (#2309)
    get_cli_session: Literal["factory.turns.get_cli_session"] = (
        "factory.turns.get_cli_session"
    )
    get_cli_session_by_pool: Literal["factory.turns.get_cli_session_by_pool"] = (
        "factory.turns.get_cli_session_by_pool"
    )
    get_resume_count: Literal["factory.turns.get_resume_count"] = (
        "factory.turns.get_resume_count"
    )
    get_last_session: Literal["factory.turns.get_last_session"] = (
        "factory.turns.get_last_session"
    )
    list_sessions: Literal["factory.turns.list_sessions"] = (
        "factory.turns.list_sessions"
    )
    list_recent_sessions: Literal["factory.turns.list_recent_sessions"] = (
        "factory.turns.list_recent_sessions"
    )
    get_turns: Literal["factory.turns.get_turns"] = "factory.turns.get_turns"
    get_turns_by_session: Literal["factory.turns.get_turns_by_session"] = (
        "factory.turns.get_turns_by_session"
    )


SUBJECTS = _Subjects()
