"""CredentialStore: SQLite-backed encrypted storage for bot tokens."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from cryptography.fernet import Fernet

from lyra.errors import KeyringError

__all__ = ["BotSecretRow", "CredentialStore", "LyraKeyring"]


_CREATE_BOT_SECRETS = """
CREATE TABLE IF NOT EXISTS bot_secrets (
    id                       INTEGER PRIMARY KEY,
    platform                 TEXT NOT NULL,
    bot_id                   TEXT NOT NULL,
    token                    TEXT NOT NULL,
    webhook_secret           TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, bot_id)
)
"""


@dataclass
class BotSecretRow:
    platform: str
    bot_id: str
    encrypted_token: str
    encrypted_webhook_secret: str | None
    updated_at: str


class LyraKeyring:
    """Manages a Fernet symmetric key stored on disk with 0o600 permissions."""

    def __init__(self, key_path: Path, key: bytes) -> None:
        self.key_path = key_path
        self.key = key

    @classmethod
    async def load_or_create(cls, key_path: Path) -> LyraKeyring:
        """Load existing key or atomically create a new one.

        Returns a LyraKeyring instance with the loaded/created key.
        """
        if key_path.exists():
            try:
                key = key_path.read_bytes()
            except PermissionError as e:
                raise KeyringError(
                    str(key_path), "not readable — check permissions"
                ) from e
            return cls(key_path, key)
        key = cls._generate_atomic(key_path)
        return cls(key_path, key)

    @staticmethod
    def _generate_atomic(key_path: Path) -> bytes:
        """Generate a new Fernet key and write it atomically with 0o600 permissions.

        Writes to a temporary file first, then renames — ensures that a partial
        write on disk-full never leaves a corrupted key file at key_path.
        """
        key = Fernet.generate_key()
        tmp_path = key_path.with_suffix(".tmp")
        success = False
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
            success = True
        finally:
            os.close(fd)
            if not success:
                tmp_path.unlink(missing_ok=True)
        os.rename(str(tmp_path), str(key_path))
        return key


class CredentialStore:
    """SQLite-backed store for encrypted bot credentials.

    Tokens are encrypted with Fernet symmetric encryption before storage,
    ensuring that the raw token never appears in plaintext in the database.
    """

    def __init__(self, db_path: str | Path, keyring: LyraKeyring) -> None:
        self._db_path = str(db_path)
        self._keyring = keyring
        self._db: aiosqlite.Connection | None = None
        self._fernet: Fernet | None = None

    async def connect(self) -> None:
        """Open DB, enable WAL, create table, initialise Fernet cipher."""
        if self._db is not None:
            return
        self._fernet = Fernet(self._keyring.key)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_BOT_SECRETS)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._fernet = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    def _require_fernet(self) -> Fernet:
        if self._fernet is None:
            raise RuntimeError("call connect() first")
        return self._fernet

    async def set(
        self,
        platform: str,
        bot_id: str,
        token: str,
        webhook_secret: str | None = None,
    ) -> None:
        """Encrypt and store credentials. Overwrites existing entry."""
        fernet = self._require_fernet()
        db = self._require_db()
        enc_token = fernet.encrypt(token.encode()).decode()
        enc_secret = (
            fernet.encrypt(webhook_secret.encode()).decode() if webhook_secret else None
        )
        await db.execute(
            """
            INSERT INTO bot_secrets
                (platform, bot_id, token, webhook_secret, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(platform, bot_id) DO UPDATE SET
                token = excluded.token,
                webhook_secret = excluded.webhook_secret,
                updated_at = excluded.updated_at
            """,
            (platform, bot_id, enc_token, enc_secret),
        )
        await db.commit()

    async def get(self, platform: str, bot_id: str) -> str | None:
        """Return the decrypted token, or None if not found."""
        fernet = self._require_fernet()
        db = self._require_db()
        async with db.execute(
            "SELECT token FROM bot_secrets WHERE platform=? AND bot_id=?",
            (platform, bot_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return fernet.decrypt(row[0].encode()).decode()

    async def get_full(
        self, platform: str, bot_id: str
    ) -> tuple[str, str | None] | None:
        """Return (decrypted_token, decrypted_webhook_secret_or_None).

        Returns None if no entry exists for the given platform/bot_id.
        """
        fernet = self._require_fernet()
        db = self._require_db()
        async with db.execute(
            "SELECT token, webhook_secret"
            " FROM bot_secrets WHERE platform=? AND bot_id=?",
            (platform, bot_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        token = fernet.decrypt(row[0].encode()).decode()
        webhook_secret = (
            fernet.decrypt(row[1].encode()).decode() if row[1] is not None else None
        )
        return token, webhook_secret

    async def exists(self, platform: str, bot_id: str) -> bool:
        db = self._require_db()
        async with db.execute(
            "SELECT 1 FROM bot_secrets WHERE platform=? AND bot_id=?",
            (platform, bot_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def delete(self, platform: str, bot_id: str) -> bool:
        """Delete credentials. Returns True if deleted, False if not found."""
        db = self._require_db()
        cursor = await db.execute(
            "DELETE FROM bot_secrets WHERE platform=? AND bot_id=?",
            (platform, bot_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def list_all(self) -> list[BotSecretRow]:
        """Return all stored bot credential rows (tokens remain encrypted)."""
        db = self._require_db()
        async with db.execute(
            "SELECT platform, bot_id, token, webhook_secret, updated_at "
            "FROM bot_secrets ORDER BY platform, bot_id"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            BotSecretRow(
                platform=r[0],
                bot_id=r[1],
                encrypted_token=r[2],
                encrypted_webhook_secret=r[3],
                updated_at=r[4],
            )
            for r in rows
        ]
