"""Unified pairing system for Telegram and Discord (issue #103).

Provides invite-code-based access control via PairingManager + PairingConfig.
Handlers live in lyra.plugins.pairing; hub pairing gate removed in #245.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.auth_store import AuthStore

from lyra.core.trust import TrustLevel

from .pairing_config import (
    _CREATE_PAIRING_CODES,
    _MAX_CODE_ATTEMPTS,
    PairingConfig,
    PairingError,
    _sha256,
    _utc_now,
)
from .sqlite_base import SqliteStore

# Re-export so existing `from lyra.core.pairing import PairingConfig/PairingError`
# imports continue to work unchanged.
__all__ = [
    "PairingConfig",
    "PairingError",
    "PairingManager",
    "get_pairing_manager",
    "set_pairing_manager",
]

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class PairingManager(SqliteStore):
    """Manages pairing codes using aiosqlite.

    On successful code validation, grants are written to AuthStore instead of
    the former paired_sessions table (removed in #245).
    Admin check (is_admin/_admin_user_ids) removed in #315.
    """

    def __init__(
        self,
        config: PairingConfig,
        db_path: str | Path,
        auth_store: AuthStore | None = None,
    ) -> None:
        super().__init__(db_path)
        self.config = config
        self._auth_store = auth_store
        # In-memory sliding window: identity_key -> deque of failure timestamps
        self._rate_timestamps: dict[str, deque[float]] = {}

    async def connect(self) -> None:
        """Open aiosqlite connection and create pairing_codes table."""
        await self._open_db(ddl=[_CREATE_PAIRING_CODES])
        log.info("PairingManager connected (db=%s)", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        await super().close()
        log.info("PairingManager closed")

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    async def generate_code(self, admin_identity: str) -> str:
        """Generate a plaintext pairing code and store its SHA-256 hash.

        Raises PairingError if max_pending codes already exist for this admin.
        """
        db = self._require_db()

        pending = await self._count_pending(admin_identity)
        if pending >= self.config.max_pending:
            raise PairingError(
                f"Max pending codes ({self.config.max_pending}) reached for "
                f"{admin_identity!r}. Revoke an existing code first."
            )

        code = "".join(
            secrets.choice(self.config.alphabet) for _ in range(self.config.code_length)
        )
        code_hash = _sha256(code)
        expires_at = _utc_now() + timedelta(seconds=self.config.ttl_seconds)

        await db.execute(
            "INSERT INTO pairing_codes (code_hash, created_by, expires_at) "
            "VALUES (?, ?, ?)",
            (code_hash, admin_identity, expires_at.isoformat()),
        )
        await db.commit()
        log.info(
            "Generated pairing code for %s (expires %s)", admin_identity, expires_at
        )
        return code

    async def _count_pending(self, admin_identity: str) -> int:
        """Count non-expired codes for this admin."""
        db = self._require_db()
        now_iso = _utc_now().isoformat()
        async with db.execute(
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
        """Validate a pairing code and grant TRUSTED access if valid.

        Returns (success, message). On success, upserts a TRUSTED grant into
        AuthStore and deletes the used code.
        """
        db = self._require_db()
        code_hash = _sha256(code)
        now = _utc_now()

        # BEGIN IMMEDIATE to prevent two concurrent /join calls from both
        # consuming the same code (TOCTOU race between SELECT and DELETE).
        await db.execute("BEGIN IMMEDIATE")
        try:
            # Increment attempt counter before checking existence so every
            # probe -- hit or miss -- is counted toward the per-code limit.
            await db.execute(
                "UPDATE pairing_codes SET attempt_count = attempt_count + 1 "
                "WHERE code_hash = ?",
                (code_hash,),
            )

            # Note: SQL WHERE code_hash = ? is not constant-time, but with SHA-256
            # input hashing and 40-bit code entropy (~1.1T combinations), timing
            # attacks are impractical at personal-use scale. See PR #124 review W5.
            async with db.execute(
                "SELECT code_hash, expires_at, attempt_count "
                "FROM pairing_codes WHERE code_hash = ?",
                (code_hash,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                await db.execute("ROLLBACK")
                return False, "Invalid code."

            _stored_hash, expires_at_iso, attempt_count = row

            if attempt_count >= _MAX_CODE_ATTEMPTS:
                await db.execute(
                    "DELETE FROM pairing_codes WHERE code_hash = ?", (code_hash,)
                )
                await db.execute("COMMIT")
                return False, "Code has been invalidated due to too many attempts."
            expires_at = datetime.fromisoformat(expires_at_iso)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if now > expires_at:
                # Clean up expired code
                await db.execute(
                    "DELETE FROM pairing_codes WHERE code_hash = ?", (code_hash,)
                )
                await db.execute("COMMIT")
                return False, "Code has expired."

            session_expires_at = now + timedelta(days=self.config.session_max_age_days)

            # Delete the used code
            await db.execute(
                "DELETE FROM pairing_codes WHERE code_hash = ?", (code_hash,)
            )
            await db.execute("COMMIT")
        except BaseException:
            await db.execute("ROLLBACK")
            raise

        # Upsert TRUSTED grant into AuthStore (outside the IMMEDIATE transaction)
        if self._auth_store is not None:
            try:
                await self._auth_store.upsert(
                    identity_key,
                    TrustLevel.TRUSTED,
                    session_expires_at,
                    granted_by="invite",
                    source=code_hash,
                )
            except Exception:
                log.exception(
                    "validate_code: failed to persist grant for %s — code consumed",
                    identity_key,
                )
                return False, "Internal error persisting grant."
            log.info(
                "Paired %s via AuthStore (session expires %s)",
                identity_key,
                session_expires_at,
            )
        else:
            log.warning(
                "validate_code: no auth_store configured, grant not persisted for %s",
                identity_key,
            )
        return True, "Successfully paired."

    # ------------------------------------------------------------------
    # Session checks
    # ------------------------------------------------------------------

    async def revoke_session(self, identity_key: str) -> bool:
        """Revoke a user's grant. Returns True if it existed."""
        if self._auth_store is not None:
            return await self._auth_store.revoke(identity_key)
        return False

    # ------------------------------------------------------------------
    # Rate limiting (in-memory sliding window)
    # ------------------------------------------------------------------

    def check_rate_limit(self, identity_key: str) -> bool:
        """Return True if the user is under the rate limit.

        Prunes timestamps outside the sliding window before checking.
        Does NOT record the attempt -- call record_failed_attempt() separately.
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
