"""NATS KV-backed message-to-session index for reply-to resume (#1059).

Phase 5 of #1049: replaces SQLite message_index.db with a NATS KV bucket
``lyra-msg-index``. TTL is configured at bucket creation time (retention_days
→ seconds). NATS handles expiry natively — no manual cleanup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import nats.errors
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import BadRequestError, KeyNotFoundError

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext
    from nats.js.kv import KeyValue

log = logging.getLogger(__name__)

KV_BUCKET = "lyra-msg-index"


def _kv_config(retention_days: int) -> KeyValueConfig:
    ttl_seconds = float(retention_days * 24 * 60 * 60)
    return KeyValueConfig(
        bucket=KV_BUCKET,
        ttl=ttl_seconds,
        storage=StorageType.FILE,
    )


async def ensure_kv(js: "JetStreamContext", retention_days: int = 90) -> "KeyValue":
    """Create or bind KV bucket lyra-msg-index idempotently.

    TTL = retention_days * 86400 seconds (default 90 days).
    Returns the bound KeyValue handle.
    """
    cfg = _kv_config(retention_days)
    try:
        kv = await js.create_key_value(cfg)
        log.info("message-index: KV bucket %s created (ttl=%.0f s)", KV_BUCKET, cfg.ttl)
        return kv
    except BadRequestError:
        log.debug("message-index: KV bucket %s already exists, binding", KV_BUCKET)
        return await js.key_value(KV_BUCKET)
    except nats.errors.Error:
        log.exception("message-index: KV bucket %s provision failed", KV_BUCKET)
        raise


def _sanitize_key_part(value: str) -> str:
    """Replace NATS subject metacharacters with a safe placeholder."""
    return value.replace(".", "_").replace("*", "_").replace(">", "_")


class MessageIndexKvStore:
    """NATS KV-backed message-to-session index for reply-to resume.

    Key format: ``<pool_id>:<platform_msg_id>`` — key parts are sanitized
    so that ``.``, ``*``, and ``>`` are replaced with ``_`` because these
    characters act as subject token separators or wildcards in NATS subjects.
    Value: ``session_id`` (UTF-8 bytes)
    """

    def __init__(self, js: "JetStreamContext", retention_days: int = 90) -> None:
        self._js = js
        self._retention_days = retention_days
        self._kv: "KeyValue | None" = None

    async def connect(self) -> None:
        """Bind to the pre-provisioned KV bucket."""
        self._kv = await self._js.key_value(KV_BUCKET)

    async def close(self) -> None:
        """Release the KV handle (no-op — NATS connection is managed externally)."""
        self._kv = None

    async def upsert(
        self,
        pool_id: str,
        platform_msg_id: str | None,
        session_id: str,
        role: Literal["user", "assistant"],
    ) -> None:
        """Index a message. Skips if platform_msg_id is None (circuit-breaker)."""
        if platform_msg_id is None:
            return
        kv = self._require_kv()
        key = f"{_sanitize_key_part(pool_id)}:{_sanitize_key_part(platform_msg_id)}"
        await kv.put(key, session_id.encode())

    async def resolve(self, pool_id: str, platform_msg_id: str) -> str | None:
        """O(1) KV lookup — return session_id or None."""
        kv = self._require_kv()
        key = f"{_sanitize_key_part(pool_id)}:{_sanitize_key_part(platform_msg_id)}"
        try:
            entry = await kv.get(key)
            return entry.value.decode() if entry.value else None
        except KeyNotFoundError:
            return None

    async def cleanup_older_than(self, days: int) -> int:
        """NATS KV handles TTL expiry natively — no-op."""
        return 0

    def _require_kv(self) -> "KeyValue":
        if self._kv is None:
            raise RuntimeError(
                "MessageIndexKvStore not connected — call connect() first"
            )
        return self._kv
