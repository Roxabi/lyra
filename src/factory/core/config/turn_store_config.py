"""TurnStoreConfig — frozen configuration constants for TurnStore read operations.

Centralises default limits so that TurnStoreProtocol method signatures and
concrete implementations stay in sync without magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TurnStoreConfig:
    """Configuration constants for TurnStore read-path operations.

    All attributes are class-level defaults; instantiation is not required.
    Consumers should reference the class attributes directly, e.g.::

        TurnStoreConfig.DEFAULT_GET_TURNS_LIMIT
    """

    DEFAULT_GET_TURNS_LIMIT: int = 50  # const-ok: named config default
    """Default maximum number of turns returned by ``get_turns``."""

    DEFAULT_LIST_SESSIONS_LIMIT: int = 5
    """Default maximum number of sessions returned by ``list_sessions``."""

    COMPACT_TURN_FETCH_LIMIT: int = 500  # const-ok: named config default
    """Bulk-read limit for compaction turn-fetch (full-history token estimate)."""

    SUMMARY_TURN_LIMIT: int = 20  # const-ok: named config default
    """Maximum number of recent turns fetched for session summary generation."""
