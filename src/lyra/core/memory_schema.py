"""Memory schema compatibility — post-connect migration for roxabi-vault.

Ensures the 'entries' table has sensible column defaults so that partial
inserts (e.g. in tests) don't crash on NOT-NULL constraints.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def apply_schema_compat(db) -> None:  # noqa: ANN001 — aiosqlite Connection
    """Add DEFAULT 'general' on the category column if missing.

    Uses the 12-step SQLite table-rename procedure to rebuild the table.
    """
    try:
        async with db.execute("PRAGMA table_info(entries)") as cur:
            cols = await cur.fetchall()
        cat_col = next((c for c in cols if c[1] == "category"), None)
        if cat_col is None:
            return
        # col tuple: (cid, name, type, notnull, dflt_value, pk)
        if cat_col[4] is not None:
            return

        await db.execute("PRAGMA foreign_keys = OFF")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS entries_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'general',
                type TEXT NOT NULL DEFAULT 'note',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                namespace TEXT NOT NULL DEFAULT 'vault',
                embedding BLOB,
                source_turns TEXT,
                event_date TEXT
            )
        """)
        await db.execute(
            "INSERT INTO entries_new SELECT id, category, type, title, content,"
            " metadata, created_at, updated_at, namespace, embedding,"
            " source_turns, event_date FROM entries"
        )
        await db.execute("DROP TABLE entries")
        await db.execute("ALTER TABLE entries_new RENAME TO entries")
        # Recreate FTS virtual table and triggers
        await db.execute("DROP TABLE IF EXISTS entries_fts")
        await db.execute("""
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                title, content, category, type,
                content=entries, content_rowid=id
            )
        """)
        await db.execute("""
            CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(rowid, title, content, category, type)
                VALUES (new.id, new.title, new.content, new.category, new.type);
            END
        """)
        await db.execute(
            "CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN"
            " INSERT INTO entries_fts(entries_fts, rowid, title, content, category, type)"  # noqa: E501
            " VALUES ('delete', old.id, old.title, old.content, old.category, old.type);"  # noqa: E501
            " END"
        )
        await db.execute(
            "CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN"
            " INSERT INTO entries_fts(entries_fts, rowid, title, content, category, type)"  # noqa: E501
            " VALUES ('delete', old.id, old.title, old.content, old.category, old.type);"  # noqa: E501
            " INSERT INTO entries_fts(rowid, title, content, category, type)"
            " VALUES (new.id, new.title, new.content, new.category, new.type);"
            " END"
        )
        await db.execute("PRAGMA foreign_keys = ON")
        await db.commit()
    except Exception:
        log.warning(
            "schema compat migration failed; database may be in inconsistent state",
            exc_info=True,
        )
