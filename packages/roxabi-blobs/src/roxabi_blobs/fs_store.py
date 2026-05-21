"""Flat-FS + SQLite implementation of `BlobStore` (single-host, v1).

Layout:
    <root>/index.sqlite          — WAL-mode manifest
    <root>/<sha[:2]>/<sha>       — content-addressed blob files

Write order (durability — see ADR-067):
    1. write file
    2. fsync(file)
    3. fsync(shard dir)          ← without this, dir entry may be lost on crash
    4. INSERT blobs (first sighting only)
    5. INSERT blob_refs          (always — per-ingestion provenance)

Concurrency:
    `_lock` (asyncio.Lock) wraps the entire write path. SQLite WAL +
    BUSY_TIMEOUT=5000 only covers OS-level (inter-process) contention;
    in-process coroutine concurrency requires the lock.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from roxabi_blobs.errors import BlobNotFoundError, BlobWriteError
from roxabi_blobs.models import BlobRef
from roxabi_blobs.schema import PRAGMA_BUSY_TIMEOUT, PRAGMA_WAL, SCHEMA_SQL


def _shard_for(content_hash: str) -> str:
    """Two-char prefix of the hash — 256 shard dirs, sufficient through ~5M blobs."""
    return content_hash[:2]


def _now_iso() -> str:
    """ISO-8601 UTC timestamp — what we store in SQLite TEXT columns."""
    return datetime.now(tz=UTC).isoformat()


class FsBlobStore:
    """Flat-FS + SQLite implementation of `BlobStore` (single-host, v1)."""

    def __init__(self, root: Path, *, db_path: Path | None = None) -> None:
        self.root = Path(root)
        self.db_path = db_path if db_path is not None else self.root / "index.sqlite"
        self._conn: aiosqlite.Connection | None = None
        # Lock instantiated lazily in __aenter__ — binds to the running
        # event loop, not whichever loop happened to be active at construct time.
        self._lock: object | None = None

    async def __aenter__(self) -> Self:
        import asyncio

        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(PRAGMA_WAL)
        await conn.execute(PRAGMA_BUSY_TIMEOUT)
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
        self._conn = conn
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._lock = None

    def _require_open(self) -> aiosqlite.Connection:
        if self._conn is None or self._lock is None:
            raise BlobWriteError("FsBlobStore used outside an `async with` context.")
        return self._conn

    async def put(  # noqa: PLR0913 — signature locked by ADR-067 §Interface
        self,
        data: bytes,
        *,
        mime: str,
        source: str,
        filename: str | None = None,
        platform_ref: str | None = None,
        platform_message_id: str | None = None,
    ) -> BlobRef:
        conn = self._require_open()
        assert self._lock is not None
        async with self._lock:  # type: ignore[union-attr]
            content_hash = hashlib.sha256(data).hexdigest()
            now = _now_iso()
            shard = _shard_for(content_hash)
            shard_dir = self.root / shard
            file_path = shard_dir / content_hash

            cursor = await conn.execute(
                "SELECT store_path, mime, size, first_seen_at FROM blobs "
                "WHERE content_hash = ?",
                (content_hash,),
            )
            existing = await cursor.fetchone()
            await cursor.close()

            if existing is None:
                shard_dir.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(file_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o644)
                    try:
                        os.write(fd, data)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    dir_fd = os.open(shard_dir, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except FileExistsError:
                    # Another process won the race; trust existing file.
                    pass
                except OSError as e:
                    raise BlobWriteError(
                        f"blob write failed for {content_hash}: {e}"
                    ) from e

                store_path = str(file_path)
                size = len(data)
                await conn.execute(
                    "INSERT INTO blobs "
                    "(content_hash, mime, size, store_path, first_seen_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (content_hash, mime, size, store_path, now),
                )
            else:
                store_path, mime, size, _ = existing
                store_path = str(store_path)
                size = int(size)

            await conn.execute(
                "INSERT INTO blob_refs (content_hash, source, platform_ref, "
                "platform_message_id, filename, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_hash,
                    source,
                    platform_ref,
                    platform_message_id,
                    filename,
                    now,
                ),
            )
            await conn.commit()

            return BlobRef(
                store_key=store_path,
                content_hash=content_hash,
                mime=mime,
                size=size,
                filename=filename,
                source=source,
                platform_ref=platform_ref,
                platform_message_id=platform_message_id,
                created_at=datetime.fromisoformat(now),
            )

    async def get(self, store_key: str) -> bytes:
        path = Path(store_key)
        try:
            return path.read_bytes()
        except FileNotFoundError as e:
            raise BlobNotFoundError(store_key) from e

    async def exists(self, content_hash: str) -> BlobRef | None:
        conn = self._require_open()
        cursor = await conn.execute(
            "SELECT b.store_path, b.mime, b.size, r.filename, r.source, "
            "       r.platform_ref, r.platform_message_id, r.ingested_at "
            "FROM blob_refs r JOIN blobs b ON r.content_hash = b.content_hash "
            "WHERE r.content_hash = ? "
            "ORDER BY r.ingested_at DESC LIMIT 1",
            (content_hash,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        (
            store_path,
            mime,
            size,
            filename,
            source,
            platform_ref,
            platform_message_id,
            ingested_at,
        ) = row
        return BlobRef(
            store_key=str(store_path),
            content_hash=content_hash,
            mime=str(mime),
            size=int(size),
            filename=filename,
            source=str(source),
            platform_ref=platform_ref,
            platform_message_id=platform_message_id,
            created_at=datetime.fromisoformat(str(ingested_at)),
        )

    async def delete(self, blob_ref_id: int) -> None:
        conn = self._require_open()
        assert self._lock is not None
        async with self._lock:  # type: ignore[union-attr]
            cursor = await conn.execute(
                "SELECT r.content_hash, b.store_path "
                "FROM blob_refs r JOIN blobs b ON r.content_hash = b.content_hash "
                "WHERE r.id = ?",
                (blob_ref_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return
            content_hash, store_path = str(row[0]), str(row[1])

            await conn.execute("DELETE FROM blob_refs WHERE id = ?", (blob_ref_id,))
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM blob_refs WHERE content_hash = ?",
                (content_hash,),
            )
            remaining_row = await cursor.fetchone()
            await cursor.close()
            remaining = int(remaining_row[0]) if remaining_row is not None else 0

            if remaining == 0:
                await conn.execute(
                    "DELETE FROM blobs WHERE content_hash = ?", (content_hash,)
                )
                try:
                    Path(store_path).unlink(missing_ok=True)
                except OSError as e:
                    raise BlobWriteError(f"unlink failed for {store_path}: {e}") from e

            await conn.commit()
