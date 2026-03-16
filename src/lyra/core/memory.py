"""Memory layer for Lyra — MemoryManager wrapping AsyncMemoryDB (roxabi-vault).

Provides:
- SessionSnapshot: frozen dataclass capturing pool state at flush time
- FRESHNESS_TTL_DAYS: per-type staleness thresholds
- MemoryManager: async context manager wrapping AsyncMemoryDB
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from roxabi_vault import AsyncMemoryDB

from lyra.core.memory_freshness import age_str, is_stale
from lyra.core.memory_schema import apply_schema_compat
from lyra.core.memory_types import FRESHNESS_TTL_DAYS, SessionSnapshot

# Re-export so `from lyra.core.memory import SessionSnapshot` keeps working.
__all__ = [
    "FRESHNESS_TTL_DAYS",
    "MemoryManager",
    "SessionSnapshot",
    "age_str",
    "is_stale",
]

log = logging.getLogger(__name__)


class MemoryManager:
    """Thin async wrapper around AsyncMemoryDB (roxabi-vault)."""

    def __init__(self, vault_path: Path | str) -> None:
        self._db = AsyncMemoryDB(vault_path)

    async def connect(self) -> None:
        await self._db.connect()
        await apply_schema_compat(self._db._db_or_raise())

    async def close(self) -> None:
        await self._db.close()

    # -- Identity anchor ---------------------------------------------------

    async def get_identity_anchor(self, namespace: str) -> str | None:
        results = await self._db.search("IDENTITY_ANCHOR", namespace, limit=1)
        anchors = [r for r in results if r.get("type") == "anchor"]
        return anchors[0]["content"] if anchors else None

    async def save_identity_anchor(self, namespace: str, text: str) -> None:
        existing = await self.get_identity_anchor(namespace)
        if existing is None:
            await self._db.save_entry(
                content=text,
                type="anchor",
                title="IDENTITY_ANCHOR",
                namespace=namespace,
            )
        else:
            db = self._db._db_or_raise()
            await db.execute(
                "UPDATE entries SET content=?, updated_at=datetime('now')"
                " WHERE type='anchor' AND namespace=? AND title='IDENTITY_ANCHOR'",
                (text, namespace),
            )
            await db.commit()

    # -- Session + contact -------------------------------------------------

    async def upsert_session(
        self,
        snap: SessionSnapshot,
        summary: str,
        status: str = "final",
    ) -> None:
        await self._db.upsert_session(
            snap.session_id,
            summary,
            user_id=snap.user_id,
            medium=snap.medium,
            agent_namespace=snap.agent_namespace,
            session_start=snap.session_start.isoformat(),
            session_end=snap.session_end.isoformat(),
            message_count=snap.message_count,
            source_turns=snap.source_turns,
            status=status,
        )

    async def upsert_contact(self, user_id: str, medium: str, namespace: str) -> None:
        db = self._db._db_or_raise()
        async with db.execute(
            "SELECT id FROM entries WHERE type='contact'"
            " AND json_extract(metadata,'$.user_id')=? AND namespace=?",
            (user_id, namespace),
        ) as cur:
            row = await cur.fetchone()
        meta = json.dumps(
            {
                "user_id": user_id,
                "medium": medium,
                "last_seen": datetime.now(UTC).isoformat(),
            }
        )
        if row:
            await db.execute(
                "UPDATE entries SET metadata=?, updated_at=datetime('now') WHERE id=?",
                (meta, row[0]),
            )
        else:
            await self._db.save_entry(
                content=user_id,
                type="contact",
                title=user_id,
                namespace=namespace,
                metadata={
                    "user_id": user_id,
                    "medium": medium,
                    "last_seen": datetime.now(UTC).isoformat(),
                },
            )
        await db.commit()

    # -- Recall (cross-session) --------------------------------------------

    async def recall(
        self,
        user_id: str,
        namespace: str,
        first_msg: str = "",
        token_budget: int = 1000,
    ) -> str:
        db = self._db._db_or_raise()
        async with db.execute(
            "SELECT id, type, namespace, metadata, content, created_at, updated_at"
            " FROM entries"
            " WHERE type='session'"
            " AND json_extract(metadata,'$.user_id')=?"
            " AND (json_extract(metadata,'$.agent_namespace')=? OR namespace=?)"
            " ORDER BY updated_at DESC LIMIT 5",
            (user_id, namespace, namespace),
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
        concepts: list[dict] = []
        if first_msg:
            concept_namespace = f"{namespace}:{user_id}"
            raw = await self._db.search(first_msg, concept_namespace, limit=8)
            concepts = [e for e in raw if e.get("type") == "concept"]
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
        )
        parts = ["[MEMORY]\n" + "\n".join(lines)] if lines else []
        if prefs_block:
            parts.append(prefs_block)
        return "\n\n".join(parts)

    async def _fetch_preferences(
        self,
        user_id: str,
        namespace: str,
        token_budget: int = 300,
    ) -> str:
        raw = await self._db.search("preference", namespace, limit=10)
        prefs = [
            e
            for e in raw
            if e.get("type") == "preference"
            and json.loads(e.get("metadata", "{}")).get("user_id") == user_id
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

    # -- Concept + preference upserts --------------------------------------

    async def upsert_concept(self, snap: SessionSnapshot, data: dict) -> None:
        name = data.get("name")
        if not name:
            log.warning(
                "upsert_concept: skipping entry with missing 'name': %r",
                list(data.keys()),
            )
            return
        db = self._db._db_or_raise()
        async with db.execute(
            "SELECT id, metadata, updated_at FROM entries"
            " WHERE type='concept' AND namespace=?"
            " AND json_extract(metadata,'$.name')=?"
            " AND json_extract(metadata,'$.user_id')=?",
            (f"{snap.agent_namespace}:{snap.user_id}", name, snap.user_id),
        ) as cur:
            row = await cur.fetchone()
        now = datetime.now(UTC)
        if row:
            existing_meta = json.loads(row[1] or "{}")
            entry_stale = is_stale(
                {"type": "concept", "metadata": row[1], "updated_at": row[2]},
            )
            if entry_stale:
                new_meta = {
                    **data,
                    "user_id": snap.user_id,
                    "source_session_id": snap.session_id,
                    "mention_count": 1,
                    "first_mentioned": now.isoformat(),
                    "last_mentioned": now.isoformat(),
                }
            else:
                existing_relations = existing_meta.get("relations", [])
                merged_relations = existing_relations + [
                    r for r in data.get("relations", []) if r not in existing_relations
                ]
                new_meta = {
                    **existing_meta,
                    **data,
                    "user_id": snap.user_id,
                    "source_session_id": snap.session_id,
                    "relations": merged_relations,
                    "mention_count": existing_meta.get("mention_count", 0) + 1,
                    "last_mentioned": now.isoformat(),
                }
            await db.execute(
                "UPDATE entries SET content=?, metadata=?, updated_at=datetime('now')"
                " WHERE id=?",
                (data["content"], json.dumps(new_meta), row[0]),
            )
        else:
            meta = {
                **data,
                "user_id": snap.user_id,
                "source_session_id": snap.session_id,
                "mention_count": 1,
                "first_mentioned": now.isoformat(),
                "last_mentioned": now.isoformat(),
            }
            await self._db.save_entry(
                content=data["content"],
                type="concept",
                title=name,
                namespace=f"{snap.agent_namespace}:{snap.user_id}",
                metadata=meta,
            )
        await db.commit()

    async def upsert_preference(self, snap: SessionSnapshot, data: dict) -> None:
        name = data.get("name")
        if not name:
            log.warning(
                "upsert_preference: skipping entry with missing 'name': %r",
                list(data.keys()),
            )
            return
        db = self._db._db_or_raise()
        async with db.execute(
            "SELECT id, metadata FROM entries"
            " WHERE type='preference' AND namespace=?"
            " AND json_extract(metadata,'$.name')=?"
            " AND json_extract(metadata,'$.user_id')=?",
            (snap.agent_namespace, name, snap.user_id),
        ) as cur:
            row = await cur.fetchone()
        if row:
            existing_meta = json.loads(row[1] or "{}")
            entry_stale = is_stale(
                {
                    "type": "preference",
                    "metadata": row[1],
                    "updated_at": existing_meta.get("last_mentioned"),
                }
            )
            new_strength = (
                data.get("strength", 0.5)
                if entry_stale
                else min(1.0, existing_meta.get("strength", 0.5) + 0.1)
            )
            new_meta = {
                **existing_meta,
                **data,
                "user_id": snap.user_id,
                "source_session_id": snap.session_id,
                "strength": new_strength,
            }
            await db.execute(
                "UPDATE entries SET content=?, metadata=?, updated_at=datetime('now')"
                " WHERE id=?",
                (data.get("content", name), json.dumps(new_meta), row[0]),
            )
        else:
            meta = {
                **data,
                "user_id": snap.user_id,
                "source_session_id": snap.session_id,
            }
            await self._db.save_entry(
                content=data.get("content", name),
                type="preference",
                title=name,
                namespace=snap.agent_namespace,
                metadata=meta,
            )
        await db.commit()
