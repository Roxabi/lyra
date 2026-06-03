"""MemoryConfig — frozen dataclass grouping MemoryManager query limits and budgets.

Extracted from bare int literals in memory.py (#1659).
All constants are pure data; no framework or infrastructure imports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration constants for MemoryManager recall queries.

    Fields:
        DEFAULT_TOKEN_BUDGET: Default token_budget for recall().
        DEFAULT_RECALL_LIMIT: LIMIT clause for the session IN-query.
        DEFAULT_CONCEPT_LIMIT: limit= arg passed to _db.search() in _fetch_concepts.
        DEFAULT_PREF_LIMIT: limit= arg passed to _db.search() in _fetch_preferences.
        DEFAULT_PREF_TOKEN_BUDGET: Cap applied to the preference sub-budget
            (min(DEFAULT_PREF_TOKEN_BUDGET, token_budget)).
    """

    DEFAULT_TOKEN_BUDGET: int = 1000  # const-ok: named config default
    DEFAULT_RECALL_LIMIT: int = 5
    DEFAULT_CONCEPT_LIMIT: int = 8
    DEFAULT_PREF_LIMIT: int = 10  # const-ok: named config default
    DEFAULT_PREF_TOKEN_BUDGET: int = 300  # const-ok: named config default
