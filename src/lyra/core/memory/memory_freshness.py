"""Memory freshness helpers — staleness detection and age formatting.

Module-level functions used by MemoryManager to determine entry freshness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from lyra.core.memory.memory_types import FRESHNESS_TTL_DAYS


def is_stale(entry: dict) -> bool:
    """Return True if *entry* is older than its type-specific TTL."""
    meta = (
        json.loads(entry.get("metadata", "{}"))
        if isinstance(entry.get("metadata"), str)
        else (entry.get("metadata") or {})
    )
    etype = entry.get("type", "")
    category = meta.get("category", "")
    key = f"{etype}:{category}" if etype == "concept" else etype
    ttl = FRESHNESS_TTL_DAYS.get(key)
    if ttl is None:
        return False
    updated_str = entry.get("updated_at", entry.get("created_at", ""))
    if not updated_str:
        return False
    updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (datetime.now(UTC) - updated).days > ttl


def age_str(entry: dict) -> str:
    """Return a human-readable age string like '42d' for *entry*."""
    updated_str = entry.get("updated_at", entry.get("created_at", ""))
    if not updated_str:
        return ""
    updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    days = (datetime.now(UTC) - updated).days
    return f"{days}d"
