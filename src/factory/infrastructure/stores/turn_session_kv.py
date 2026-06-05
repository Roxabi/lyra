"""NATS KV-backed last-session store for adapter pool-to-session resume (#1721).

Mirrors ``message_index_kv.py`` (#1059).  TTL is set at bucket creation time
(retention_days → seconds); NATS handles expiry natively.

Bucket name: ``factory-turns-meta`` — MUST byte-match the ACL subject prefix
``$KV.factory-turns-meta.>`` (D8).  Do not rename.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyNotFoundError,
    NoKeysError,
)

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext
    from nats.js.kv import KeyValue

log = logging.getLogger(__name__)

KV_BUCKET = "factory-turns-meta"


def _kv_config(retention_days: int) -> KeyValueConfig:
    ttl_seconds = float(retention_days * 24 * 60 * 60)
    return KeyValueConfig(
        bucket=KV_BUCKET,
        ttl=ttl_seconds,
        storage=StorageType.FILE,
    )


async def ensure_kv(js: "JetStreamContext", retention_days: int = 90) -> "KeyValue":
    """Create or bind KV bucket factory-turns-meta idempotently.

    TTL = retention_days * 86400 seconds (default 90 days).
    Returns the bound KeyValue handle.
    """
    cfg = _kv_config(retention_days)
    try:
        kv = await js.create_key_value(cfg)
        log.info("turns-meta: KV bucket %s created (ttl=%.0f s)", KV_BUCKET, cfg.ttl)
        return kv
    except BadRequestError:
        log.debug("turns-meta: KV bucket %s already exists, binding", KV_BUCKET)
        return await js.key_value(KV_BUCKET)
    except nats.errors.Error:
        log.exception("turns-meta: KV bucket %s provision failed", KV_BUCKET)
        raise


class KvLastSessionStore:
    """NATS KV-backed ``LastSessionStore`` implementation.

    Key format: ``last_session.<pool_id>``
    Value: ``session_id`` (UTF-8 bytes)

    Cold-boot safety
    ----------------
    ``connect()`` binds to a pre-provisioned bucket.  If the bucket does not
    exist yet (adapter boots before hub provisions it), ``self._kv`` is set to
    ``None`` and a warning is logged — the store degrades gracefully: every
    ``get_last_session`` returns ``None`` (new-session path) and
    ``set_last_session`` is a no-op.  Other NATS errors from ``connect()``
    propagate; callers must handle them.
    """

    def __init__(self, js: "JetStreamContext", retention_days: int = 90) -> None:
        self._js = js
        self._retention_days = retention_days
        self._kv: "KeyValue | None" = None

    async def connect(self) -> None:
        """Bind to an existing ``factory-turns-meta`` bucket.

        Degrades (``_kv=None`` + warning) on ``BucketNotFoundError`` — the hub
        has not provisioned the bucket yet (cold-boot).  All other NATS errors
        propagate; callers must handle them.
        """
        try:
            self._kv = await self._js.key_value(KV_BUCKET)
        except BucketNotFoundError:
            # Cold-boot degradation: hub has not provisioned the bucket yet.
            # All other nats.errors.Error (ConnectionClosedError,
            # AuthorizationError, …) propagate — they indicate real problems
            # that the caller must handle (#7).
            log.warning(
                "turns-meta: KV bucket %s not found during bind — "
                "last-session store will degrade to new-session until hub "
                "provisions the bucket",
                KV_BUCKET,
            )
            self._kv = None

    async def close(self) -> None:
        """Release the KV handle (no-op — NATS connection managed externally)."""
        self._kv = None

    async def get_last_session(self, pool_id: str) -> str | None:
        """Return the most-recent session_id for *pool_id*, or ``None`` on miss."""
        if self._kv is None:
            return None
        try:
            entry = await self._kv.get(f"last_session.{pool_id}")
            return entry.value.decode() if entry.value else None
        except (KeyNotFoundError, NoKeysError):
            return None

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        """Persist *session_id* as the most-recent session for *pool_id*."""
        if self._kv is None:
            return
        try:
            await self._kv.put(f"last_session.{pool_id}", session_id.encode())
        except (nats.errors.TimeoutError, nats.errors.ConnectionClosedError):
            # Transient connection errors only: degrade silently (#44).
            # AuthorizationError, BadRequestError, and other non-transient
            # nats.errors.Error subclasses propagate to signal configuration
            # or permission problems that require operator attention.
            log.warning(
                "turns-meta: set_last_session failed pool_id=%s — "
                "next message will start a new session",
                pool_id,
            )
