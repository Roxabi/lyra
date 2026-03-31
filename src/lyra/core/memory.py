"""Memory layer for Lyra — MemoryManager wrapping AsyncMemoryDB (roxabi-vault).

Provides:
- SessionSnapshot: frozen dataclass capturing pool state at flush time
- FRESHNESS_TTL_DAYS: per-type staleness thresholds
- MemoryManager: async context manager wrapping AsyncMemoryDB
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from roxabi_vault import AsyncMemoryDB

if TYPE_CHECKING:
    from lyra.core.stores.identity_alias_store import IdentityAliasStore

from lyra.core.memory_freshness import age_str, is_stale
from lyra.core.memory_schema import apply_schema_compat
from lyra.core.memory_types import FRESHNESS_TTL_DAYS, SessionSnapshot
from lyra.core.memory_upserts import MemoryManagerUpserts

# Re-export so `from lyra.core.memory import SessionSnapshot` keeps working.
__all__ = [
    "FRESHNESS_TTL_DAYS",
    "MemoryManager",
    "SessionSnapshot",
    "age_str",
    "is_stale",
]

log = logging.getLogger(__name__)


class MemoryManager(MemoryManagerUpserts):
    """Thin async wrapper around AsyncMemoryDB (roxabi-vault)."""

    def __init__(self, vault_path: Path | str) -> None:
        self._db = AsyncMemoryDB(vault_path)
        self._alias_store: IdentityAliasStore | None = None

    def set_alias_store(self, store: IdentityAliasStore) -> None:
        """Wire up the alias store for cross-platform memory lookups."""
        self._alias_store = store

    async def connect(self) -> None:
        await self._db.connect()
        await apply_schema_compat(self._db._db_or_raise())

    async def close(self) -> None:
        await self._db.close()

    # -- Identity anchor (read) --------------------------------------------

    async def get_identity_anchor(self, namespace: str) -> str | None:
        results = await self._db.search("IDENTITY_ANCHOR", namespace, limit=1)
        anchors = [r for r in results if r.get("type") == "anchor"]
        return anchors[0]["content"] if anchors else None

    # -- Recall (cross-session) --------------------------------------------

    async def recall(
        self,
        user_id: str,
        namespace: str,
        first_msg: str = "",
        token_budget: int = 1000,
    ) -> str:
        # Resolve aliases once; used by session query, concept search, and prefs
        if self._alias_store is not None:
            aliases = self._alias_store.resolve_aliases(user_id)
        else:
            aliases = frozenset({user_id})
        alias_list = tuple(aliases)

        db = self._db._db_or_raise()

        # Session query with IN clause covering all aliases
        placeholders = ", ".join("?" * len(alias_list))
        async with db.execute(
            "SELECT id, type, namespace, metadata, content, created_at, updated_at"
            " FROM entries"
            " WHERE type='session'"
            f" AND json_extract(metadata,'$.user_id') IN ({placeholders})"
            " AND (json_extract(metadata,'$.agent_namespace')=? OR namespace=?)"
            " ORDER BY updated_at DESC LIMIT 5",
            (*alias_list, namespace, namespace),
        ) as cur:
            rows = await cur.fetchall()

        col_names = [
            "id",
            "type",
            "namespace",
            "metadata",
            "content",
            "created_at",
            "updated_at",
        ]
        user_sessions = [dict(zip(col_names, r)) for r in rows]

        # Concept search across all alias namespaces, deduplicating by entry id
        concepts = await self._fetch_concepts(first_msg, namespace, aliases)

        fresh_entries, stale_entries = [], []
        for e in user_sessions + concepts:
            (stale_entries if is_stale(e) else fresh_entries).append(e)
        lines: list[str] = []
        tokens_used = 0
        for e in fresh_entries + stale_entries:
            age = age_str(e) if e in stale_entries else ""
            prefix = f"[~{age}] " if age else ""
            line = f"- {prefix}{e['content'][:200]}"
            tokens_used += len(line) // 4
            if tokens_used > token_budget:
                break
            lines.append(line)
        prefs_block = await self._fetch_preferences(
            user_id,
            namespace,
            token_budget=min(300, token_budget),
            aliases=aliases,
        )
        parts = ["[MEMORY]\n" + "\n".join(lines)] if lines else []
        if prefs_block:
            parts.append(prefs_block)
        return "\n\n".join(parts)

    async def _fetch_concepts(
        self,
        first_msg: str,
        namespace: str,
        aliases: frozenset[str],
    ) -> list[dict]:
        """Search concept entries across all alias namespaces, deduplicating by id."""
        if not first_msg:
            return []
        results: list[dict] = []
        seen_ids: set[int] = set()
        for alias in aliases:
            concept_namespace = f"{namespace}:{alias}"
            raw = await self._db.search(first_msg, concept_namespace, limit=8)
            for e in raw:
                entry_id: int | None = e.get("id")
                if e.get("type") == "concept" and entry_id not in seen_ids:
                    results.append(e)
                    if entry_id is not None:
                        seen_ids.add(entry_id)
        return results

    async def _fetch_preferences(
        self,
        user_id: str,
        namespace: str,
        token_budget: int = 300,
        aliases: frozenset[str] | None = None,
    ) -> str:
        effective_aliases: frozenset[str] = (
            aliases if aliases is not None else frozenset({user_id})
        )
        raw = await self._db.search("preference", namespace, limit=10)
        prefs = [
            e
            for e in raw
            if e.get("type") == "preference"
            and json.loads(e.get("metadata", "{}")).get("user_id") in effective_aliases
        ]
        lines = []
        tokens_used = 0
        for p in sorted(prefs, key=lambda e: is_stale(e)):
            meta = json.loads(p.get("metadata", "{}"))
            name = meta.get("name", p["content"][:60])
            age = f" [~{age_str(p)}]" if is_stale(p) else ""
            line = f"- {name}{age}"
            tokens_used += len(line) // 4
            if tokens_used > token_budget:
                break
            lines.append(line)
        return "[PREFERENCES]\n" + "\n".join(lines) if lines else ""
