"""Wire-key resolution helpers for the blobstore HTTP service.

Canonical wire form: ``sha256:<hex>`` (content address).
On-disk form: absolute path stored in the ``blobs.store_path`` column.

These functions translate between the two without touching FsBlobStore internals
beyond the manifest SQLite connection (already exposed by ``_handlers._conn``).
"""

from __future__ import annotations

import aiosqlite

from roxabi_blobs import FsBlobStore
from roxabi_blobs.errors import BlobNotFoundError


def _conn(store: FsBlobStore) -> aiosqlite.Connection:
    """Return the live aiosqlite connection from an open FsBlobStore.

    Raises ``RuntimeError`` if the store is not open — callers let this
    propagate; ``handle_get`` maps it to 500.
    """
    if store._conn is None:  # noqa: SLF001
        raise RuntimeError("FsBlobStore not open")
    return store._conn  # noqa: SLF001


async def resolve_wire_key(store: FsBlobStore, store_key: str) -> str:
    """Map a wire ``store_key`` to an on-disk ``store_path``.

    Canonical wire form is ``sha256:<hex>`` (content address). Resolve it to
    the on-disk path via the manifest. A key WITHOUT the prefix is treated as a
    legacy ``store_path`` and returned unchanged (back-compat for in-flight refs).
    """
    if not store_key.startswith("sha256:"):
        return store_key
    content_hash = store_key[len("sha256:") :]
    conn = _conn(store)  # raises RuntimeError if store not open → 500 via handle_get
    cursor = await conn.execute(
        "SELECT store_path FROM blobs WHERE content_hash = ?", (content_hash,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise BlobNotFoundError("blob not found")
    return str(row[0])


async def resolve_delete_key(conn: aiosqlite.Connection, key: str) -> int | None:
    """Resolve a non-numeric DELETE key to a ``blob_refs.id``.

    Accepts ``sha256:<hex>`` (canonical wire key) or a legacy ``store_path``.
    Returns the latest ``blob_refs.id`` for the key, or ``None`` if not found.
    Raises ``aiosqlite.Error`` on DB failure (caller maps to 500).
    """
    if key.startswith("sha256:"):
        cursor = await conn.execute(
            "SELECT r.id FROM blob_refs r "
            "WHERE r.content_hash = ? "
            "ORDER BY r.ingested_at DESC LIMIT 1",
            (key.removeprefix("sha256:"),),
        )
    else:
        cursor = await conn.execute(
            "SELECT r.id FROM blob_refs r "
            "JOIN blobs b ON r.content_hash = b.content_hash "
            "WHERE b.store_path = ? "
            "ORDER BY r.ingested_at DESC LIMIT 1",
            (key,),
        )
    row = await cursor.fetchone()
    await cursor.close()
    return int(row[0]) if row is not None else None
