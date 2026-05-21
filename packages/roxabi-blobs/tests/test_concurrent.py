"""Concurrent `put` serialisation — 50 coroutines, one process, same blob.

`asyncio.Lock` wraps the entire write path; without it, two coroutines
could race past the dedup SELECT and both try to write the same file or
INSERT colliding `blobs` rows. WAL + `BUSY_TIMEOUT` covers inter-process
contention only.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from roxabi_blobs import FsBlobStore


async def test_50_concurrent_puts_same_blob(store: FsBlobStore) -> None:
    data = b"concurrent payload"
    expected_hash = hashlib.sha256(data).hexdigest()

    async def one_put(i: int) -> str:
        ref = await store.put(
            data,
            mime="text/plain",
            source="telegram",
            platform_ref=f"tg:{i}",
        )
        return ref.content_hash

    results = await asyncio.gather(*(one_put(i) for i in range(50)))
    assert all(h == expected_hash for h in results)

    # One file on FS, one blobs row, fifty blob_refs rows.
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
    ref_count = await (await conn.execute("SELECT COUNT(*) FROM blob_refs")).fetchone()
    assert blob_count is not None and int(blob_count[0]) == 1
    assert ref_count is not None and int(ref_count[0]) == 50

    shard_dir = store.root / expected_hash[:2]
    files = list(shard_dir.iterdir())
    assert len(files) == 1


async def test_concurrent_puts_different_blobs(store: FsBlobStore) -> None:
    """Different content → independent shards, all 10 succeed in parallel."""

    async def one(i: int) -> str:
        ref = await store.put(f"unique-{i}".encode(), mime="text/plain", source="t")
        return ref.content_hash

    hashes = await asyncio.gather(*(one(i) for i in range(10)))
    assert len(set(hashes)) == 10

    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
    assert blob_count is not None and int(blob_count[0]) == 10


async def test_lock_actually_serializes_with_forced_yield(
    store: FsBlobStore,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """consensus S5 — falsifier for the asyncio.Lock.

    Force a yield point between the dedup SELECT and the INSERT so the
    coroutine scheduler can interleave. Without the lock, two parallel
    `put` calls of the same content can both find no existing row and
    both attempt to write the file — second one hits `FileExistsError`
    or `IntegrityError`. With the lock, exactly one writes, the other
    sees the existing row.
    """
    real_lookup = store._lookup_existing  # type: ignore[attr-defined]

    async def slow_lookup(conn: object, content_hash: str) -> object:
        result = await real_lookup(conn, content_hash)
        # yield to scheduler — gives other coroutines a chance to interleave
        await asyncio.sleep(0)
        return result

    monkeypatch.setattr(store, "_lookup_existing", slow_lookup)

    data = b"forced-yield-test"
    refs = await asyncio.gather(
        store.put(data, mime="t", source="a"),
        store.put(data, mime="t", source="b"),
    )
    # Both calls succeed AND see exactly 1 file on FS, 1 blobs row, 2 refs.
    assert refs[0].content_hash == refs[1].content_hash
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
    ref_count = await (await conn.execute("SELECT COUNT(*) FROM blob_refs")).fetchone()
    assert blob_count is not None and int(blob_count[0]) == 1
    assert ref_count is not None and int(ref_count[0]) == 2
