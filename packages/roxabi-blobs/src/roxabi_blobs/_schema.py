"""SQLite schema for the BlobStore manifest.

Split tables: `blobs` (content-addressed PK) ⇔ `blob_refs` (per-ingestion
provenance, many-to-one). The `(content_hash, ingested_at DESC)` index makes
`exists()` deterministic: `ORDER BY ingested_at DESC LIMIT 1` returns the
latest ref without a sort.

Pragmas (WAL + BUSY_TIMEOUT) are applied separately by `FsBlobStore` during
`__aenter__`. They are connection-scoped, not part of the DDL.

DDL is shipped as a tuple of statements (¬ a single `executescript` blob)
because `aiosqlite.executescript`/`sqlite3.executescript` implicitly commits
any open transaction before running — a footgun for future migration code
(consensus W3).
"""

from __future__ import annotations

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS blobs (
        content_hash   TEXT PRIMARY KEY,
        mime           TEXT NOT NULL,
        size           INTEGER NOT NULL,
        store_path     TEXT NOT NULL,
        first_seen_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blob_refs (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash         TEXT NOT NULL REFERENCES blobs(content_hash),
        source               TEXT NOT NULL,
        platform_ref         TEXT,
        platform_message_id  TEXT,
        filename             TEXT,
        ingested_at          TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_blob_refs_hash_ingested
        ON blob_refs(content_hash, ingested_at DESC)
    """,
)

PRAGMA_WAL = "PRAGMA journal_mode=WAL;"
PRAGMA_BUSY_TIMEOUT = "PRAGMA busy_timeout=5000;"
