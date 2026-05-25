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
    """consensus S5 + iter-2 W2 — strong falsifier for the `asyncio.Lock`.

    Two checks:

    1. **Acquisition count** — instrument the store's `_lock.acquire` so
       we can assert it was awaited exactly once per `put` call. Remove
       `async with lock` from `put` and the count drops to 0.
    2. **Mutual exclusion** — track `_lookup_existing` in-flight count.
       Under a working lock the maximum is 1; without the lock the
       forced yield lets a second coroutine observe an in-flight value
       of 2 before the first one returns.
    """
    real_lookup = store._lookup_existing  # type: ignore[attr-defined]
    in_flight = 0
    max_in_flight = 0

    async def slow_lookup(conn: object, content_hash: str) -> object:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            # Hand control back to the scheduler — without the lock a
            # second coroutine will enter here before this one returns.
            await asyncio.sleep(0)
            return await real_lookup(conn, content_hash)
        finally:
            in_flight -= 1

    monkeypatch.setattr(store, "_lookup_existing", slow_lookup)

    lock = store._lock  # type: ignore[attr-defined]
    assert lock is not None
    real_acquire = lock.acquire
    acquire_calls = 0

    async def counting_acquire() -> bool:
        nonlocal acquire_calls
        acquire_calls += 1
        return await real_acquire()

    monkeypatch.setattr(lock, "acquire", counting_acquire)

    data = b"forced-yield-test"
    refs = await asyncio.gather(
        store.put(data, mime="t", source="a"),
        store.put(data, mime="t", source="b"),
    )

    # Falsifier 1 — lock was acquired once per put.
    assert acquire_calls == 2, (
        f"expected 2 lock acquisitions (one per put), got {acquire_calls}"
    )
    # Falsifier 2 — `_lookup_existing` was never in-flight concurrently.
    assert max_in_flight == 1, (
        f"lock failed to serialise — max concurrent _lookup_existing was "
        f"{max_in_flight}"
    )
    # Sanity — final state is correct.
    assert refs[0].content_hash == refs[1].content_hash
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
    ref_count = await (await conn.execute("SELECT COUNT(*) FROM blob_refs")).fetchone()
    assert blob_count is not None and int(blob_count[0]) == 1
    assert ref_count is not None and int(ref_count[0]) == 2
