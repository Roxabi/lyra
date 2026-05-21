"""Happy-path + edge-case tests for `FsBlobStore`."""

from __future__ import annotations

from pathlib import Path

import pytest

from roxabi_blobs import BlobNotFoundError, BlobRef, FsBlobStore


class TestRoundTrip:
    async def test_put_then_get(self, store: FsBlobStore, sample_bytes: bytes) -> None:
        ref = await store.put(sample_bytes, mime="text/plain", source="telegram")
        assert isinstance(ref, BlobRef)
        assert ref.size == len(sample_bytes)
        assert ref.mime == "text/plain"
        assert ref.source == "telegram"
        data = await store.get(ref.store_key)
        assert data == sample_bytes

    async def test_put_records_content_hash(
        self, store: FsBlobStore, sample_bytes: bytes
    ) -> None:
        import hashlib

        ref = await store.put(sample_bytes, mime="text/plain", source="discord")
        assert ref.content_hash == hashlib.sha256(sample_bytes).hexdigest()

    async def test_put_full_envelope_fields(self, store: FsBlobStore) -> None:
        ref = await store.put(
            b"x",
            mime="image/png",
            source="telegram",
            filename="cat.png",
            platform_ref="tg:42",
            platform_message_id="msg-7",
        )
        assert ref.filename == "cat.png"
        assert ref.platform_ref == "tg:42"
        assert ref.platform_message_id == "msg-7"

    async def test_shard_layout(self, store: FsBlobStore, sample_bytes: bytes) -> None:
        ref = await store.put(sample_bytes, mime="text/plain", source="t")
        path = Path(ref.store_key)
        assert path.exists()
        # <root>/<sha[:2]>/<sha>
        assert path.parent.name == ref.content_hash[:2]
        assert path.name == ref.content_hash


class TestExists:
    async def test_exists_returns_latest_ref(self, store: FsBlobStore) -> None:
        first = await store.put(
            b"data", mime="text/plain", source="telegram", platform_ref="tg:1"
        )
        await store.put(
            b"data", mime="text/plain", source="discord", platform_ref="dc:2"
        )
        found = await store.exists(first.content_hash)
        assert found is not None
        # Latest = the discord ingestion
        assert found.source == "discord"
        assert found.platform_ref == "dc:2"

    async def test_exists_missing(self, store: FsBlobStore) -> None:
        result = await store.exists("deadbeef" * 8)
        assert result is None


class TestGetErrors:
    async def test_get_missing_raises(self, store: FsBlobStore) -> None:
        with pytest.raises(BlobNotFoundError):
            await store.get("/nonexistent/path/abc")


class TestLifecycle:
    async def test_use_outside_context_raises(self, tmp_path: Path) -> None:
        s = FsBlobStore(tmp_path)
        from roxabi_blobs import BlobWriteError

        with pytest.raises(BlobWriteError):
            await s.put(b"x", mime="t", source="s")
