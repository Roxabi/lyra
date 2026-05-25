"""Flat-FS + SQLite implementation of `BlobStore` (single-host, v1).

Layout:
    <root>/index.sqlite          — WAL-mode manifest
    <root>/<sha[:2]>/<sha>       — content-addressed blob files

Write order — `put` (durability — see ADR-067):
    1. write file
    2. fsync(file)
    3. fsync(shard dir)          ← without this, dir entry may be lost on crash
    4. INSERT OR IGNORE blobs (first sighting only; idempotent under cross-proc race)
    5. INSERT blob_refs          (always — per-ingestion provenance)
    6. commit

Write order — `delete` (consensus B2: commit-before-unlink):
    1. DELETE blob_refs row
    2. count remaining refs; if 0: DELETE blobs row
    3. commit
    4. unlink file (only if final ref was dropped)
    Crash window leaves an orphan file (V6 reconciler trivially handles content-
    addressed orphans); inverse order would leave a phantom row (unrecoverable
    permanent `BlobConsistencyError`).

Concurrency:
    `_lock` (asyncio.Lock) wraps the write paths AND `exists()` (consensus B8).
    SQLite WAL + BUSY_TIMEOUT=5000 only covers OS-level (inter-process) contention.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite

from roxabi_blobs._schema import PRAGMA_BUSY_TIMEOUT, PRAGMA_WAL, SCHEMA_STATEMENTS
from roxabi_blobs.errors import (
    BlobConsistencyError,
    BlobNotFoundError,
    BlobStateError,
    BlobWriteError,
)
from roxabi_blobs.models import BlobRef


def _shard_for(content_hash: str) -> str:
    """Two-char prefix of the hash — 256 shard dirs, ~5M blobs."""
    return content_hash[:2]


def _now() -> datetime:
    """Timezone-aware UTC `datetime`."""
    return datetime.now(tz=UTC)


def _safe_resolve_in_root(candidate: str, root: Path) -> Path | None:
    """Resolve `candidate` and verify containment in `root`. None on escape."""
    try:
        resolved = Path(candidate).resolve()
        if resolved.is_relative_to(root.resolve()):
            return resolved
    except (OSError, ValueError):
        pass
    return None


class FsBlobStore:
    """Flat-FS + SQLite implementation of `BlobStore` (single-host, v1)."""

    def __init__(
        self,
        root: Path,
        *,
        db_path: Path | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.root = Path(root)
        self.db_path = db_path if db_path is not None else self.root / "index.sqlite"
        self.max_bytes = max_bytes
        self._conn: aiosqlite.Connection | None = None
        self._lock: asyncio.Lock | None = None

    async def __aenter__(self) -> Self:
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        conn = await aiosqlite.connect(self.db_path)
        # Apply pragmas + DDL via individual execute() calls — `executescript`
        # auto-commits any open transaction (consensus W3).
        await conn.execute(PRAGMA_WAL)
        await conn.execute(PRAGMA_BUSY_TIMEOUT)
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(stmt)
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

    def _require_open(self) -> tuple[aiosqlite.Connection, asyncio.Lock]:
        """Lifecycle guard — raises `BlobStateError` (neutral re. read/write)."""
        if self._conn is None or self._lock is None:
            raise BlobStateError("FsBlobStore used outside an `async with` context.")
        return self._conn, self._lock

    def _conn_ro(self) -> aiosqlite.Connection:
        """Return the open connection for read-only callers (no write-lock needed).

        WAL read-isolation makes lock-skip safe: SQLite WAL mode lets readers see a
        consistent snapshot without blocking writers; the write-lock is the writer's
        concern, not the reader's.
        """
        conn, _ = self._require_open()
        return conn

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
        if self.max_bytes is not None and len(data) > self.max_bytes:
            raise BlobWriteError(
                f"blob size {len(data)} exceeds max_bytes={self.max_bytes}"
            )
        conn, lock = self._require_open()
        async with lock:
            content_hash = hashlib.sha256(data).hexdigest()
            now = _now()
            shard = _shard_for(content_hash)
            shard_dir = self.root / shard
            file_path = shard_dir / content_hash

            existing = await self._lookup_existing(conn, content_hash)
            if existing is None:
                await self._write_blob_file(file_path, shard_dir, data, content_hash)
                store_path = str(file_path)
                size = len(data)
                # INSERT OR IGNORE — cheap defense vs cross-process race winners
                # (consensus B4). PRIMARY KEY guarantees a NO-OP on collision.
                await conn.execute(
                    "INSERT OR IGNORE INTO blobs "
                    "(content_hash, mime, size, store_path, first_seen_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (content_hash, mime, size, store_path, now.isoformat()),
                )
                # Re-read the canonical row in case the IGNORE fired.
                canonical = await self._lookup_existing(conn, content_hash)
                if canonical is not None:
                    store_path, mime, size = canonical
            else:
                store_path, mime, size = existing

            ref_cur = await conn.execute(
                "INSERT INTO blob_refs (content_hash, source, platform_ref, "
                "platform_message_id, filename, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_hash,
                    source,
                    platform_ref,
                    platform_message_id,
                    filename,
                    now.isoformat(),
                ),
            )
            blob_ref_id = ref_cur.lastrowid
            await conn.commit()

            return BlobRef(
                id=blob_ref_id,
                store_key=store_path,
                content_hash=content_hash,
                mime=mime,
                size=size,
                filename=filename,
                source=source,
                platform_ref=platform_ref,
                platform_message_id=platform_message_id,
                created_at=now,
            )

    @staticmethod
    async def _lookup_existing(
        conn: aiosqlite.Connection, content_hash: str
    ) -> tuple[str, str, int] | None:
        cursor = await conn.execute(
            "SELECT store_path, mime, size FROM blobs WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return (str(row[0]), str(row[1]), int(row[2])) if row is not None else None

    @staticmethod
    async def _write_blob_file(
        file_path: Path, shard_dir: Path, data: bytes, content_hash: str
    ) -> None:
        """`file → fsync(file) → fsync(shard_dir)`; cleanup partial file on OSError."""
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
            # Inter-process race: another writer won O_EXCL. Trust the
            # existing file (content-addressed → same bytes by definition).
            pass
        except OSError as e:
            # Clean up partial file (consensus W2) — without this, a later
            # put of the same content hits FileExistsError + reads wrong bytes.
            file_path.unlink(missing_ok=True)
            raise BlobWriteError(
                f"blob write failed for {content_hash[:12]}…: {e.strerror or 'OSError'}"
            ) from e

    async def get(self, store_key: str) -> bytes:
        # Lifecycle guard first (consensus B6) — uniform with put/exists/delete.
        self._require_open()
        # Path traversal guard (consensus B1) — resolve + containment check;
        # static error message regardless of branch (no oracle).
        resolved = _safe_resolve_in_root(store_key, self.root)
        if resolved is None:
            raise BlobNotFoundError("blob not found")
        try:
            return resolved.read_bytes()
        except FileNotFoundError as e:
            raise BlobNotFoundError("blob not found") from e

    async def exists(self, content_hash: str) -> BlobRef | None:
        conn, lock = self._require_open()
        async with lock:  # consensus B8 — eliminate TOCTOU vs concurrent delete
            try:
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
            except aiosqlite.Error as e:
                # consensus B5 — never leak raw aiosqlite.Error across boundary
                raise BlobConsistencyError(
                    f"manifest read failed for content_hash={content_hash[:12]}…"
                ) from e

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
        conn, lock = self._require_open()
        async with lock:
            try:
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

                drop_file = remaining == 0
                if drop_file:
                    await conn.execute(
                        "DELETE FROM blobs WHERE content_hash = ?", (content_hash,)
                    )
                # consensus B2 — commit BEFORE unlink. Orphan file is benign
                # (V6 reconciler reclaims by content_hash); phantom row is not.
                await conn.commit()
            except aiosqlite.Error as e:
                # Static message — do not leak SQLite schema details (column /
                # table / constraint names) across the package boundary.
                raise BlobWriteError(
                    f"delete failed for blob_ref_id={blob_ref_id}"
                ) from e

            if drop_file:
                resolved = _safe_resolve_in_root(store_path, self.root)
                if resolved is not None:
                    try:
                        resolved.unlink(missing_ok=True)
                    except OSError as e:
                        raise BlobWriteError(
                            f"unlink failed for content_hash={content_hash[:12]}…: "
                            f"{e.strerror or 'OSError'}"
                        ) from e
