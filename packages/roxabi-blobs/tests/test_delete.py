"""`delete` semantics — designed in V1, ref-count-driven cascade.

Invariants:
  - delete(id) removes exactly one `blob_refs` row
  - file + `blobs` row remain while ANY `blob_refs` row references the hash
  - file + `blobs` row are removed when the final `blob_refs` row is deleted
"""

from __future__ import annotations

from roxabi_blobs import FsBlobStore


async def _put_n(store: FsBlobStore, data: bytes, n: int) -> tuple[str, list[int]]:
    """Helper — put `data` n times; return (content_hash, [blob_ref_ids])."""
    hash_ = ""
    for i in range(n):
        ref = await store.put(data, mime="text/plain", source=f"src{i}")
        hash_ = ref.content_hash
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    cursor = await conn.execute(
        "SELECT id FROM blob_refs WHERE content_hash = ? ORDER BY id ASC",
        (hash_,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return hash_, [int(r[0]) for r in rows]


async def test_delete_one_of_two_keeps_file(store: FsBlobStore) -> None:
    data = b"keep me"
    content_hash, ids = await _put_n(store, data, 2)
    assert len(ids) == 2
    file_path = store.root / content_hash[:2] / content_hash
    assert file_path.exists()

    await store.delete(ids[0])

    # File still there; one blob_refs row remains; blobs row remains.
    assert file_path.exists()
    found = await store.exists(content_hash)
    assert found is not None
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    refs_left = await (
        await conn.execute(
            "SELECT COUNT(*) FROM blob_refs WHERE content_hash = ?", (content_hash,)
        )
    ).fetchone()
    assert refs_left is not None and int(refs_left[0]) == 1


async def test_delete_all_drops_file_and_blobs_row(store: FsBlobStore) -> None:
    data = b"all gone"
    content_hash, ids = await _put_n(store, data, 2)
    file_path = store.root / content_hash[:2] / content_hash
    assert file_path.exists()

    for blob_ref_id in ids:
        await store.delete(blob_ref_id)

    assert not file_path.exists(), "file must be unlinked at zero remaining refs"
    found = await store.exists(content_hash)
    assert found is None
    conn = store._conn  # type: ignore[attr-defined]
    assert conn is not None
    blobs_row = await (
        await conn.execute(
            "SELECT COUNT(*) FROM blobs WHERE content_hash = ?", (content_hash,)
        )
    ).fetchone()
    assert blobs_row is not None and int(blobs_row[0]) == 0


async def test_delete_unknown_id_is_noop(store: FsBlobStore) -> None:
    # delete on an id that never existed — must not raise
    await store.delete(blob_ref_id=99999)
