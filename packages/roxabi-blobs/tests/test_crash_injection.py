"""Write-ordering invariants — recover from crashes mid-`put`.

`put` phases: file write → fsync → fsync(dir) → INSERT blobs → INSERT blob_refs.
Strategy: instead of `os.kill(SIGKILL)` (POSIX-only, brittle), monkeypatch a
SQLite commit to raise mid-flight and inspect the FS state. Invariant: **if
the SQLite step fails, the blob file is still on disk in its shard path AND
no inconsistent blob_refs row was committed**.

A V6 reconciler will later sweep orphan files; that is not in scope for V1,
but V1 must not leave a state the reconciler cannot heal.
"""

from __future__ import annotations

import errno
import hashlib
import os

import pytest

from roxabi_blobs import BlobConsistencyError, BlobWriteError, FsBlobStore


async def test_file_persists_after_commit_failure(
    store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `INSERT blob_refs` fails, the FS file must still be on disk —
    an orphan that a future reconciler can re-link or GC.
    """
    data = b"crash test payload"
    expected_hash = hashlib.sha256(data).hexdigest()
    expected_path = store.root / expected_hash[:2] / expected_hash

    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None

    real_commit = conn.commit
    call_count = {"n": 0}

    async def failing_commit() -> None:  # mimics aiosqlite Connection.commit
        call_count["n"] += 1
        # Fail the FIRST commit (after blob file + INSERT blobs + INSERT blob_refs)
        if call_count["n"] == 1:
            raise RuntimeError("synthetic crash: commit failed")
        await real_commit()

    monkeypatch.setattr(conn, "commit", failing_commit)

    with pytest.raises(RuntimeError, match="synthetic crash"):
        await store.put(data, mime="text/plain", source="test")

    msg = (
        f"orphan blob file missing at {expected_path}: write-ordering regression "
        "(file → fsync → fsync(dir) → INSERT must persist file before SQLite)"
    )
    assert expected_path.exists(), msg


async def test_put_calls_fsync_twice(
    store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """consensus S3 — instrument `os.fsync` and assert it's called for
    BOTH the file fd AND the shard dir fd. Removing the dir-fsync from
    fs_store.py causes this assertion to fail (real falsifier — unlike
    the previous structural alias test).
    """
    calls = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)

    await store.put(b"x" * 16, mime="t", source="s")
    # exactly 2 fsync calls per put: file fd + shard dir fd
    assert len(calls) == 2, (
        f"expected 2 fsync calls (file + shard dir), got {len(calls)} — "
        "regression of write-ordering invariant"
    )


async def test_put_oserror_cleans_up_partial_file(
    store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """consensus W2 / S1 — disk-full mid-write must unlink the partial
    file so a subsequent put with the same content does not silently
    read the wrong bytes via `FileExistsError` fallthrough.
    """
    data = b"will-fail-mid-write"
    expected_hash = hashlib.sha256(data).hexdigest()
    expected_path = store.root / expected_hash[:2] / expected_hash

    real_write = os.write

    def failing_write(fd: int, payload: bytes) -> int:
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "write", failing_write)

    with pytest.raises(BlobWriteError, match="no space"):
        await store.put(data, mime="t", source="s")

    monkeypatch.setattr(os, "write", real_write)

    # Partial file MUST have been cleaned up — otherwise next put of same
    # content hits FileExistsError + returns stale bytes.
    assert not expected_path.exists(), "partial file leaked on OSError"


async def test_exists_wraps_aiosqlite_error(
    store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """consensus B5 — `exists` wraps raw aiosqlite.Error as `BlobConsistencyError`."""
    import aiosqlite

    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None

    async def failing_execute(*args: object, **kwargs: object) -> object:
        raise aiosqlite.OperationalError("synthetic manifest read failure")

    monkeypatch.setattr(conn, "execute", failing_execute)

    with pytest.raises(BlobConsistencyError, match="manifest read failed"):
        await store.exists("deadbeef" * 8)


def test_module_imports_in_isolation() -> None:
    """Stub — ensures the test module can be imported (sanity check)."""
    from roxabi_blobs import fs_store

    assert hasattr(fs_store, "FsBlobStore")
    # write-order sentinel: shard helper exists and produces a 2-char key
    assert len(fs_store._shard_for("abcdef")) == 2
