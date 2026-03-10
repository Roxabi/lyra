"""Unified pairing system for Telegram and Discord (issue #103).

Provides invite-code-based access control via PairingManager + PairingConfig.
Handlers live in lyra.plugins.pairing; hub gate lives in lyra.core.hub.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_SAFE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_CREATE_PAIRING_CODES = """
CREATE TABLE IF NOT EXISTS pairing_codes (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_CREATE_PAIRED_SESSIONS = """
CREATE TABLE IF NOT EXISTS paired_sessions (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    paired_by_code_hash TEXT NOT NULL,
    paired_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class PairingConfig:
    """Immutable configuration for the pairing system."""

    alphabet: str = _SAFE_ALPHABET
    code_length: int = 8
    ttl_seconds: int = 3600
    max_pending: int = 3
    session_max_age_days: int = 30
    rate_limit_attempts: int = 5
    rate_limit_window: int = 300
    enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> PairingConfig:
        """Create PairingConfig from a dict (e.g. TOML [pairing] section).

        Missing keys use field defaults.
        """
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class PairingManager:
    """Manages pairing codes and paired sessions using aiosqlite."""

    def __init__(
        self,
        config: PairingConfig,
        db_path: str | Path,
        admin_user_ids: set[str],
    ) -> None:
        self.config = config
        self._db_path = str(db_path)
        self._admin_user_ids = admin_user_ids
        self._db: aiosqlite.Connection | None = None
        # In-memory sliding window: identity_key → deque of failure timestamps
        self._rate_timestamps: dict[str, deque[float]] = {}

    async def connect(self) -> None:
        """Open aiosqlite connection and create tables."""
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_PAIRING_CODES)
        await self._db.execute(_CREATE_PAIRED_SESSIONS)
        await self._db.commit()
        log.info("PairingManager connected (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("PairingManager closed")

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    async def generate_code(self, admin_identity: str) -> str:
        """Generate a plaintext pairing code and store its SHA-256 hash.

        Raises RuntimeError if max_pending codes already exist for this admin.
        """
        if self._db is None:
            raise RuntimeError("call connect() first")

        pending = await self._count_pending(admin_identity)
        if pending >= self.config.max_pending:
            raise RuntimeError(
                f"Max pending codes ({self.config.max_pending}) reached for "
                f"{admin_identity!r}. Revoke an existing code first."
            )

        code = "".join(
            secrets.choice(self.config.alphabet) for _ in range(self.config.code_length)
        )
        code_hash = _sha256(code)
        expires_at = _utc_now() + timedelta(seconds=self.config.ttl_seconds)

        await self._db.execute(
            "INSERT INTO pairing_codes (code_hash, created_by, expires_at) "
            "VALUES (?, ?, ?)",
            (code_hash, admin_identity, expires_at.isoformat()),
        )
        await self._db.commit()
        log.info(
            "Generated pairing code for %s (expires %s)", admin_identity, expires_at
        )
        return code

    async def _count_pending(self, admin_identity: str) -> int:
        """Count non-expired codes for this admin."""
        if self._db is None:
            raise RuntimeError("call connect() first")
        now_iso = _utc_now().isoformat()
        async with self._db.execute(
            "SELECT COUNT(*) FROM pairing_codes "
            "WHERE created_by = ? AND expires_at > ?",
            (admin_identity, now_iso),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ------------------------------------------------------------------
    # Code validation
    # ------------------------------------------------------------------

    async def validate_code(self, code: str, identity_key: str) -> tuple[bool, str]:
        """Validate a pairing code and create a session if valid.

        Returns (success, message). On success, creates/replaces session and
        deletes the used code.
        """
        if self._db is None:
            raise RuntimeError("call connect() first")
        code_hash = _sha256(code)
        now = _utc_now()

        # BEGIN IMMEDIATE to prevent two concurrent /join calls from both
        # consuming the same code (TOCTOU race between SELECT and DELETE).
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            async with self._db.execute(
                "SELECT code_hash, expires_at FROM pairing_codes WHERE code_hash = ?",
                (code_hash,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                await self._db.execute("ROLLBACK")
                return False, "Invalid code."

            _stored_hash, expires_at_iso = row
            expires_at = datetime.fromisoformat(expires_at_iso)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if now > expires_at:
                # Clean up expired code
                await self._db.execute(
                    "DELETE FROM pairing_codes WHERE code_hash = ?", (code_hash,)
                )
                await self._db.execute("COMMIT")
                return False, "Code has expired."

            session_expires_at = now + timedelta(days=self.config.session_max_age_days)

            # Upsert session (replace if already paired — extends expiry)
            await self._db.execute(
                "INSERT INTO paired_sessions "
                "(identity_key, paired_by_code_hash, expires_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(identity_key) DO UPDATE SET "
                "paired_by_code_hash = excluded.paired_by_code_hash, "
                "paired_at = datetime('now'), "
                "expires_at = excluded.expires_at",
                (identity_key, code_hash, session_expires_at.isoformat()),
            )
            # Delete the used code
            await self._db.execute(
                "DELETE FROM pairing_codes WHERE code_hash = ?", (code_hash,)
            )
            await self._db.execute("COMMIT")
        except BaseException:
            await self._db.execute("ROLLBACK")
            raise
        log.info("Paired %s (session expires %s)", identity_key, session_expires_at)
        return True, "Successfully paired."

    # ------------------------------------------------------------------
    # Session checks
    # ------------------------------------------------------------------

    async def is_paired(self, identity_key: str) -> bool:
        """Return True if user has a valid (non-expired) session.

        Admin users always return True. Expired sessions are lazily deleted.
        """
        if identity_key in self._admin_user_ids:
            return True

        if self._db is None:
            raise RuntimeError("call connect() first")

        async with self._db.execute(
            "SELECT expires_at FROM paired_sessions WHERE identity_key = ?",
            (identity_key,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False

        expires_at_iso = row[0]
        expires_at = datetime.fromisoformat(expires_at_iso)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if _utc_now() > expires_at:
            # Lazy cleanup: delete expired session
            await self._db.execute(
                "DELETE FROM paired_sessions WHERE identity_key = ?",
                (identity_key,),
            )
            await self._db.commit()
            log.debug("Lazily deleted expired session for %s", identity_key)
            return False

        return True

    async def revoke_session(self, identity_key: str) -> bool:
        """Delete a paired session. Returns True if it existed."""
        if self._db is None:
            raise RuntimeError("call connect() first")

        async with self._db.execute(
            "SELECT id FROM paired_sessions WHERE identity_key = ?",
            (identity_key,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False

        await self._db.execute(
            "DELETE FROM paired_sessions WHERE identity_key = ?",
            (identity_key,),
        )
        await self._db.commit()
        log.info("Revoked session for %s", identity_key)
        return True

    # ------------------------------------------------------------------
    # Rate limiting (in-memory sliding window)
    # ------------------------------------------------------------------

    def check_rate_limit(self, identity_key: str) -> bool:
        """Return True if the user is under the rate limit.

        Prunes timestamps outside the sliding window before checking.
        Does NOT record the attempt — call record_failed_attempt() separately.
        """
        now = time.monotonic()
        window_start = now - self.config.rate_limit_window
        timestamps = self._rate_timestamps.get(identity_key)

        if timestamps is not None:
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            if not timestamps:
                del self._rate_timestamps[identity_key]
                return True
            if len(timestamps) >= self.config.rate_limit_attempts:
                return False

        return True

    def record_failed_attempt(self, identity_key: str) -> None:
        """Record a failed /join attempt timestamp for rate limiting."""
        now = time.monotonic()
        if identity_key not in self._rate_timestamps:
            self._rate_timestamps[identity_key] = deque()
        self._rate_timestamps[identity_key].append(now)


# ---------------------------------------------------------------------------
# Module-level DI for plugin handlers
# ---------------------------------------------------------------------------

_pairing_manager: PairingManager | None = None


def get_pairing_manager() -> PairingManager | None:
    """Return the module-level PairingManager (set by __main__.py)."""
    return _pairing_manager


def set_pairing_manager(pm: PairingManager | None) -> None:
    """Set the module-level PairingManager (called from __main__.py)."""
    global _pairing_manager
    _pairing_manager = pm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
