"""Happy-path + edge-case tests for `FsBlobStore`."""

from __future__ import annotations

from pathlib import Path

import pytest

from roxabi_blobs import (
    BlobNotFoundError,
    BlobRef,
    BlobStateError,
    BlobWriteError,
    FsBlobStore,
)


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

    async def test_put_empty_bytes(self, store: FsBlobStore) -> None:
        """consensus S8 — zero-byte blobs are allowed (`BlobRef.size: ge=0`)."""
        import hashlib

        ref = await store.put(b"", mime="application/octet-stream", source="test")
        assert ref.size == 0
        assert ref.content_hash == hashlib.sha256(b"").hexdigest()
        assert Path(ref.store_key).exists()
        data = await store.get(ref.store_key)
        assert data == b""

    async def test_put_created_at_is_utc(self, store: FsBlobStore) -> None:
        """consensus W5 — returned `created_at` is tz-aware."""
        ref = await store.put(b"x", mime="t", source="s")
        assert ref.created_at.tzinfo is not None


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
    async def test_get_missing_raises(self, store: FsBlobStore, tmp_path: Path) -> None:
        """A non-existent file inside the root → `BlobNotFoundError`."""
        # Craft a `store_key` that resolves under root but points to no file.
        phantom = str(tmp_path / "aa" / "deadbeef")
        with pytest.raises(BlobNotFoundError, match="blob not found"):
            await store.get(phantom)


class TestMaxBytes:
    """consensus W6 — optional `max_bytes` cap on `put()` data."""

    async def test_oversize_put_raises(self, tmp_path: Path) -> None:
        async with FsBlobStore(tmp_path, max_bytes=8) as s:
            with pytest.raises(BlobWriteError, match="exceeds max_bytes"):
                await s.put(b"123456789", mime="t", source="s")

    async def test_under_cap_put_succeeds(self, tmp_path: Path) -> None:
        async with FsBlobStore(tmp_path, max_bytes=8) as s:
            ref = await s.put(b"12345678", mime="t", source="s")
            assert ref.size == 8

    async def test_no_cap_default(self, store: FsBlobStore) -> None:
        ref = await store.put(b"x" * 10_000, mime="t", source="s")
        assert ref.size == 10_000


class TestLifecycle:
    """consensus B3 + B6 + S4 — `BlobStateError` for all 4 methods outside context."""

    @pytest.mark.parametrize(
        "method,args,kwargs",
        [
            ("put", (b"x",), {"mime": "t", "source": "s"}),
            ("get", ("/tmp/aa/abc",), {}),
            ("exists", ("abc",), {}),
            ("delete", (1,), {}),
        ],
    )
    async def test_method_outside_context_raises_state_error(
        self,
        tmp_path: Path,
        method: str,
        args: tuple,
        kwargs: dict,
    ) -> None:
        s = FsBlobStore(tmp_path)
        with pytest.raises(BlobStateError):
            await getattr(s, method)(*args, **kwargs)
