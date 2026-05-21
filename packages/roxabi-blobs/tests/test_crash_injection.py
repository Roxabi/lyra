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

import hashlib

import pytest

from roxabi_blobs import FsBlobStore


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

    # File MUST still exist on FS — this is the recoverability invariant.
    msg = (
        f"orphan blob file missing at {expected_path}: write-ordering regression "
        "(file → fsync → fsync(dir) → INSERT must persist file before SQLite)"
    )
    assert expected_path.exists(), msg


async def test_dir_fsync_keeps_file_in_directory_listing(store: FsBlobStore) -> None:
    """Sentinel: even if SQLite is rolled back, a directory walk should
    find the orphan blob file. Without `fsync(shard dir)` after the file
    fsync, ext4 crash recovery can lose the dirent — the file inode is
    flushed but the directory entry isn't.

    This test is a structural sentinel: it asserts that after a `put`
    the shard dir lists the file. It cannot directly exercise the
    fsync(dir) call without instrumenting the kernel, but it locks in
    the visible behaviour the fsync(dir) protects.
    """
    data = b"shard-dir sentinel"
    expected_hash = hashlib.sha256(data).hexdigest()
    await store.put(data, mime="text/plain", source="test")
    shard_dir = store.root / expected_hash[:2]
    files = {p.name for p in shard_dir.iterdir()}
    assert expected_hash in files, "shard dir entry must list the blob file"


def test_module_imports_in_isolation() -> None:
    """Stub — ensures the test module can be imported even on platforms
    where the monkeypatch fixture isn't available (sanity check).
    """
    from roxabi_blobs import fs_store

    assert hasattr(fs_store, "FsBlobStore")
    # write-order sentinel: shard helper exists and produces a 2-char key
    assert len(fs_store._shard_for("abcdef")) == 2
