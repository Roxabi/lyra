"""`sweep_older_than` semantics — age-based blob retention (#1836).

Invariants:
  - Refs whose ingested_at < cutoff_ts are deleted (ref-counted).
  - File survives when a second ref to the same content_hash is younger than
    the cutoff (partial sweep — only the old ref is removed).
  - File is unlinked when all refs to a content_hash are swept.
  - Empty store returns (0, 0) — no-op.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from roxabi_blobs import FsBlobStore


async def _put_with_ts(
    store: FsBlobStore, data: bytes, ingested_at: float, source: str = "src"
) -> int:
    """Put `data` then back-date its `ingested_at` to an ISO-8601 string.

    Matches the format produced by production put() (datetime.isoformat()).
    Storing a raw float would mask the TEXT-vs-REAL affinity bug in sweep_older_than.

    Returns the blob_ref_id.
    """
    ref = await store.put(data, mime="text/plain", source=source)
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    ingested_at_iso = datetime.fromtimestamp(ingested_at, tz=UTC).isoformat()
    await conn.execute(
        "UPDATE blob_refs SET ingested_at = ? WHERE content_hash = ? AND source = ?",
        (ingested_at_iso, ref.content_hash, source),
    )
    await conn.commit()
    # Retrieve the assigned id.
    cursor = await conn.execute(
        "SELECT id FROM blob_refs"
        " WHERE content_hash = ? AND source = ?"
        " ORDER BY id DESC LIMIT 1",
        (ref.content_hash, source),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_sweep_empty_store_is_noop(store: FsBlobStore) -> None:
    cutoff = time.time()
    refs_deleted, files_unlinked = await store.sweep_older_than(cutoff)
    assert refs_deleted == 0
    assert files_unlinked == 0


@pytest.mark.asyncio
async def test_sweep_file_survives_when_younger_ref_exists(
    store: FsBlobStore, sample_bytes: bytes
) -> None:
    """Second ref to the same content_hash is younger → file must survive."""
    old_ts = time.time() - 200.0
    now_ts = time.time()
    cutoff = time.time() - 100.0  # old_ts is before cutoff; now_ts is after

    # Same content ingested twice with different timestamps.
    await _put_with_ts(store, sample_bytes, old_ts, source="old")
    await _put_with_ts(store, sample_bytes, now_ts, source="new")

    # Determine content_hash and expected file path.
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    cursor = await conn.execute("SELECT content_hash FROM blobs LIMIT 1")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    content_hash = str(row[0])
    file_path = store.root / content_hash[:2] / content_hash

    assert file_path.exists(), "file must exist before sweep"

    refs_deleted, files_unlinked = await store.sweep_older_than(cutoff)

    assert refs_deleted == 1, "only the old ref should be deleted"
    assert files_unlinked == 0, "file must survive because younger ref still exists"
    assert file_path.exists(), "file must still be on disk"

    # Confirm one blob_refs row remains.
    cursor = await conn.execute("SELECT COUNT(*) FROM blob_refs")
    count_row = await cursor.fetchone()
    await cursor.close()
    assert count_row is not None
    assert int(count_row[0]) == 1


@pytest.mark.asyncio
async def test_sweep_all_refs_unlinks_file(
    store: FsBlobStore, sample_bytes: bytes
) -> None:
    """When all refs are older than the cutoff, file must be unlinked."""
    old_ts = time.time() - 200.0
    cutoff = time.time() - 100.0

    await _put_with_ts(store, sample_bytes, old_ts, source="only")

    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    cursor = await conn.execute("SELECT content_hash FROM blobs LIMIT 1")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    content_hash = str(row[0])
    file_path = store.root / content_hash[:2] / content_hash

    assert file_path.exists(), "file must exist before sweep"

    refs_deleted, files_unlinked = await store.sweep_older_than(cutoff)

    assert refs_deleted == 1
    assert files_unlinked == 1
    assert not file_path.exists(), "file must be unlinked after final ref is swept"

    # Both tables must be empty.
    cursor = await conn.execute("SELECT COUNT(*) FROM blob_refs")
    count_row = await cursor.fetchone()
    await cursor.close()
    assert count_row is not None and int(count_row[0]) == 0

    cursor = await conn.execute("SELECT COUNT(*) FROM blobs")
    count_row = await cursor.fetchone()
    await cursor.close()
    assert count_row is not None and int(count_row[0]) == 0
