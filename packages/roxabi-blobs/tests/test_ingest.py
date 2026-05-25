"""Tests for `ingest_bytes_to_blob_ref` — shared eager-ingest helper (#1333).

Coverage targets:
- Round-trip: ingest → BlobRef → read back via store.get
- Dedup: second call with same bytes → existing ref, no file rewrite
- Parallel: 10 concurrent ingests of same bytes → only 1 file written
- Crash recovery: partial file (orphan, no SQLite row) → re-ingest succeeds
- Public import path: from roxabi_blobs.ingest and from roxabi_blobs (top-level)
- BlobRef fields: all optional fields forwarded correctly
- lyra-agnostic: ingest.py imports only roxabi_blobs-internal symbols
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from roxabi_blobs import FsBlobStore, ingest_bytes_to_blob_ref
from roxabi_blobs.ingest import ingest_bytes_to_blob_ref as ingest_direct
from roxabi_blobs.models import BlobRef

if TYPE_CHECKING:
    pass


class TestRoundTrip:
    async def test_ingest_returns_blob_ref(self, store: FsBlobStore) -> None:
        data = b"hello from telegram_voice"
        ref = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="telegram_voice"
        )
        assert isinstance(ref, BlobRef)
        assert ref.size == len(data)
        assert ref.mime == "audio/ogg"
        assert ref.source == "telegram_voice"

    async def test_ingest_content_hash_matches_sha256(self, store: FsBlobStore) -> None:
        data = b"discord audio payload"
        ref = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/webm", source="discord_audio"
        )
        assert ref.content_hash == hashlib.sha256(data).hexdigest()

    async def test_ingest_then_get_round_trip(self, store: FsBlobStore) -> None:
        data = b"round-trip payload"
        ref = await ingest_bytes_to_blob_ref(
            store, data, mime="application/octet-stream", source="slack_audio"
        )
        retrieved = await store.get(ref.store_key)
        assert retrieved == data

    async def test_ingest_all_optional_fields_forwarded(
        self, store: FsBlobStore
    ) -> None:
        data = b"full-envelope test"
        ref = await ingest_bytes_to_blob_ref(
            store,
            data,
            mime="audio/mp4",
            source="telegram_voice",
            platform_ref="tg:file_id_abc",
            platform_message_id="msg-42",
            filename="voice_message.m4a",
        )
        assert ref.platform_ref == "tg:file_id_abc"
        assert ref.platform_message_id == "msg-42"
        assert ref.filename == "voice_message.m4a"

    async def test_ingest_source_stored_verbatim(self, store: FsBlobStore) -> None:
        """source is not validated — arbitrary future adapter names pass through."""
        for source in ["telegram_voice", "discord_audio", "slack_audio", "cli_file"]:
            ref = await ingest_bytes_to_blob_ref(
                store,
                f"data-{source}".encode(),
                mime="audio/ogg",
                source=source,
            )
            assert ref.source == source

    async def test_ingest_created_at_is_tz_aware(self, store: FsBlobStore) -> None:
        ref = await ingest_bytes_to_blob_ref(
            store, b"tz test", mime="text/plain", source="test"
        )
        assert ref.created_at.tzinfo is not None


class TestDedup:
    async def test_second_ingest_same_bytes_no_file_rewrite(
        self, store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup hit: same bytes ingested twice → file written only once.

        We instrument `os.open` to count `O_CREAT | O_EXCL` calls — these
        only happen during the actual file-write path. After the first ingest
        the file exists; the second call hits `FileExistsError` (content-
        addressed race guard in FsBlobStore), so no second write occurs.
        """
        data = b"same bytes dedup test"
        real_open = os.open
        creat_calls = 0

        def counting_open(path: object, flags: int, mode: int = 0o777) -> int:
            nonlocal creat_calls
            if flags & os.O_CREAT:
                creat_calls += 1
            return real_open(path, flags, mode)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "open", counting_open)

        ref1 = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="telegram_voice"
        )
        first_creat_count = creat_calls

        ref2 = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="discord_audio"
        )

        # First ingest opened the file for creation; second ingest hit the
        # FileExistsError guard and did NOT re-open with O_CREAT.
        assert creat_calls == first_creat_count, (
            f"expected no new O_CREAT calls on second ingest "
            f"(dedup), but creat_calls went from {first_creat_count} to {creat_calls}"
        )
        assert ref1.content_hash == ref2.content_hash
        assert ref1.store_key == ref2.store_key

    async def test_second_ingest_new_blob_ref_row(self, store: FsBlobStore) -> None:
        """Dedup hit still records a new blob_refs provenance row per ADR-067."""
        data = b"provenance dedup test"
        await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="telegram_voice"
        )
        await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="discord_audio"
        )

        conn = store._conn  # type: ignore[attr-defined]
        assert conn is not None
        blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
        ref_count = await (
            await conn.execute("SELECT COUNT(*) FROM blob_refs")
        ).fetchone()
        assert blob_count is not None and int(blob_count[0]) == 1
        assert ref_count is not None and int(ref_count[0]) == 2

    async def test_dedup_file_content_unchanged(self, store: FsBlobStore) -> None:
        data = b"content must match"
        ref1 = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="s1"
        )
        ref2 = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="s2"
        )
        assert await store.get(ref1.store_key) == data
        assert await store.get(ref2.store_key) == data


class TestParallel:
    async def test_10_concurrent_ingests_same_bytes(self, store: FsBlobStore) -> None:
        """10 concurrent ingests of the same bytes → exactly 1 file on FS."""
        data = b"parallel ingest payload"
        expected_hash = hashlib.sha256(data).hexdigest()

        refs = await asyncio.gather(
            *(
                ingest_bytes_to_blob_ref(
                    store,
                    data,
                    mime="audio/ogg",
                    source="telegram_voice",
                    platform_ref=f"tg:{i}",
                )
                for i in range(10)
            )
        )

        assert all(r.content_hash == expected_hash for r in refs)

        shard_dir = store.root / expected_hash[:2]
        files = list(shard_dir.iterdir())
        assert len(files) == 1, (
            f"expected 1 file after parallel ingest, found {len(files)}"
        )

        conn = store._conn  # type: ignore[attr-defined]
        assert conn is not None
        blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
        ref_count = await (
            await conn.execute("SELECT COUNT(*) FROM blob_refs")
        ).fetchone()
        assert blob_count is not None and int(blob_count[0]) == 1
        assert ref_count is not None and int(ref_count[0]) == 10

    async def test_10_concurrent_ingests_different_bytes(
        self, store: FsBlobStore
    ) -> None:
        """10 concurrent ingests of distinct payloads → 10 independent files."""
        refs = await asyncio.gather(
            *(
                ingest_bytes_to_blob_ref(
                    store,
                    f"unique-{i}".encode(),
                    mime="audio/ogg",
                    source="telegram_voice",
                )
                for i in range(10)
            )
        )

        hashes = {r.content_hash for r in refs}
        assert len(hashes) == 10

        conn = store._conn  # type: ignore[attr-defined]
        assert conn is not None
        blob_count = await (await conn.execute("SELECT COUNT(*) FROM blobs")).fetchone()
        assert blob_count is not None and int(blob_count[0]) == 10


class TestCrashRecovery:
    async def test_partial_file_no_sqlite_entry_reingest_succeeds(
        self, store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Crash scenario: blob file on FS but no SQLite row.

        Simulate: write the raw file bytes directly (bypassing store.put),
        without any SQLite rows. A subsequent `ingest_bytes_to_blob_ref`
        should succeed — the `FileExistsError` guard in FsBlobStore treats
        an existing file as the authoritative content (content-addressed),
        inserts the blobs row via INSERT OR IGNORE, and records a blob_refs row.
        """
        data = b"crash recovery orphan blob"
        content_hash = hashlib.sha256(data).hexdigest()
        shard_dir = store.root / content_hash[:2]
        shard_dir.mkdir(parents=True, exist_ok=True)
        orphan_path = shard_dir / content_hash

        # Write the orphan file manually — simulates a crash after FS write
        # but before the SQLite commit.
        orphan_path.write_bytes(data)

        # No SQLite rows yet
        conn = store._conn  # type: ignore[attr-defined]
        assert conn is not None
        row = await (
            await conn.execute(
                "SELECT COUNT(*) FROM blobs WHERE content_hash = ?", (content_hash,)
            )
        ).fetchone()
        assert row is not None and int(row[0]) == 0

        # Re-ingest must succeed and recover the ref
        ref = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="telegram_voice"
        )
        assert ref.content_hash == content_hash
        assert await store.get(ref.store_key) == data

        # SQLite now has the row
        row = await (
            await conn.execute(
                "SELECT COUNT(*) FROM blobs WHERE content_hash = ?", (content_hash,)
            )
        ).fetchone()
        assert row is not None and int(row[0]) == 1

    async def test_partial_write_oserror_then_reingest_succeeds(
        self, store: FsBlobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed first ingest (OSError mid-write) leaves no partial file.

        After the cleanup by FsBlobStore._write_blob_file, a second call to
        ingest_bytes_to_blob_ref with the same bytes must complete successfully.
        """
        import errno

        data = b"partial write recovery"
        real_write = os.write
        fail_once = {"done": False}

        def failing_write(fd: int, payload: bytes) -> int:
            if not fail_once["done"]:
                fail_once["done"] = True
                raise OSError(errno.ENOSPC, "no space left on device")
            return real_write(fd, payload)

        monkeypatch.setattr(os, "write", failing_write)

        from roxabi_blobs import BlobWriteError

        with pytest.raises(BlobWriteError):
            await ingest_bytes_to_blob_ref(
                store, data, mime="audio/ogg", source="telegram_voice"
            )

        monkeypatch.setattr(os, "write", real_write)

        # Second ingest must succeed — partial file was cleaned up
        ref = await ingest_bytes_to_blob_ref(
            store, data, mime="audio/ogg", source="telegram_voice"
        )
        assert ref.content_hash == hashlib.sha256(data).hexdigest()
        assert await store.get(ref.store_key) == data


class TestPublicImport:
    def test_importable_from_ingest_module(self) -> None:
        from roxabi_blobs.ingest import ingest_bytes_to_blob_ref as fn

        assert callable(fn)

    def test_importable_from_package_top_level(self) -> None:
        from roxabi_blobs import ingest_bytes_to_blob_ref as fn

        assert callable(fn)

    def test_direct_import_is_same_object(self) -> None:
        assert ingest_bytes_to_blob_ref is ingest_direct

    def test_no_lyra_imports_in_ingest_module(self) -> None:
        """Verify zero lyra.* coupling — ingest.py is lyra-agnostic."""
        import ast
        import importlib.util

        spec = importlib.util.find_spec("roxabi_blobs.ingest")
        assert spec is not None and spec.origin is not None
        source = Path(spec.origin).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("lyra"), (
                        f"ingest.py must not import lyra.*: found '{node.module}'"
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("lyra"), (
                            f"ingest.py must not import lyra.*: found '{alias.name}'"
                        )


class TestProtocolAgnostic:
    async def test_works_with_mock_blob_store(self) -> None:
        """ingest_bytes_to_blob_ref accepts any BlobStore-conformant object."""
        from datetime import UTC, datetime

        expected_ref = BlobRef(
            store_key="/mock/ab/abcdef",
            content_hash="abcdef",
            mime="audio/ogg",
            size=5,
            source="telegram_voice",
            created_at=datetime.now(tz=UTC),
        )
        mock_store = AsyncMock()
        mock_store.put = AsyncMock(return_value=expected_ref)

        result = await ingest_bytes_to_blob_ref(
            mock_store,
            b"hello",
            mime="audio/ogg",
            source="telegram_voice",
            platform_ref="tg:123",
        )

        assert result is expected_ref
        mock_store.put.assert_called_once_with(
            b"hello",
            mime="audio/ogg",
            source="telegram_voice",
            platform_ref="tg:123",
            platform_message_id=None,
            filename=None,
        )
