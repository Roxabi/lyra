"""Content-addressed dedup: same blob N× → 1 file + 1 `blobs` row + N `blob_refs`."""

from __future__ import annotations

from pathlib import Path

from roxabi_blobs import FsBlobStore


async def test_five_puts_same_blob_dedupe(store: FsBlobStore) -> None:
    data = b"five times the same"
    refs = []
    for i in range(5):
        ref = await store.put(
            data,
            mime="text/plain",
            source=["telegram", "discord", "stt", "tts", "manual"][i],
            platform_ref=f"src:{i}",
        )
        refs.append(ref)

    # All refs share the same content_hash and store_key
    hashes = {r.content_hash for r in refs}
    keys = {r.store_key for r in refs}
    assert len(hashes) == 1
    assert len(keys) == 1

    # Exactly one file on FS at <root>/<sha[:2]>/<sha>
    file_path = Path(refs[0].store_key)
    assert file_path.exists()
    shard_dir = file_path.parent
    files_in_shard = list(shard_dir.iterdir())
    assert len(files_in_shard) == 1

    # SQLite: 1 row in blobs, 5 rows in blob_refs
    conn = store._conn  # type: ignore[attr-defined]  # white-box inspection for dedup invariants
    assert conn is not None
    blob_row_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
    ref_row_count = await (
        await conn.execute("SELECT COUNT(*) FROM blob_refs")
    ).fetchone()
    assert blob_row_count is not None and int(blob_row_count[0]) == 1
    assert ref_row_count is not None and int(ref_row_count[0]) == 5
