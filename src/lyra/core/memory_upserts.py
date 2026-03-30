"""Upsert (write) methods for MemoryManager — split from memory.py (epic #293).

Provides MemoryManagerUpserts, a mixin base class containing all write
operations.  MemoryManager (memory.py) inherits from this to keep recall/read
logic separate while preserving the existing public API.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lyra.core.memory_freshness import is_stale
from lyra.core.memory_types import SessionSnapshot

log = logging.getLogger(__name__)


class MemoryManagerUpserts:
    """Mixin providing upsert/write methods for MemoryManager.

    Expects ``self._db`` to be an ``AsyncMemoryDB`` instance (set by
    ``MemoryManager.__init__``).
    """

    if TYPE_CHECKING:
        from roxabi_vault import AsyncMemoryDB

        _db: AsyncMemoryDB

        async def get_identity_anchor(self, namespace: str) -> str | None: ...

    # -- Identity anchor (write) -------------------------------------------

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

    # -- Session + contact (write) -----------------------------------------

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
            source_session_id=snap.session_id,  # queryable metadata (#417 / S5)
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

    # -- Concept + preference upserts (write) ------------------------------

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
