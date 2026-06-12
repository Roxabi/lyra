"""NATS KV-backed active-jobs registry (#1796).

Implements ``ActiveJobsPort`` (``factory.core.ports.active_jobs``).

Key layout
----------
Per-job entry::

    job.<job_id_safe>  →  JSON(ActiveJobEntry fields)

Pool→job singleton index (steer / queue modes only)::

    idx.<pool_id_safe>  →  <job_id>   (ASCII bytes, checked via CAS)

Both keys live in the ``factory-active-jobs`` bucket (TTL = ACTIVE_JOBS_TTL).
The index key is re-put on every ``refresh()`` to reset its TTL in step with
the job key; this keeps them co-expiring.

Constants
---------
ACTIVE_JOBS_BUCKET:
    JetStream KV bucket name.
ACTIVE_JOBS_TTL:
    Bucket-level TTL in **seconds** (120 s).  NATS expires keys automatically.
ACTIVE_JOBS_REFRESH:
    Recommended client-side refresh cadence in **seconds** (30 s).
    Callers should call ``refresh()`` at this interval so entries never expire.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
    NotFoundError,
)

from factory.core.ports.active_jobs import ActiveJobEntry, RegistryConflictError
from factory.infrastructure.stores._kv_keys import kv_safe_part

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext
    from nats.js.kv import KeyValue

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ACTIVE_JOBS_BUCKET = "factory-active-jobs"
ACTIVE_JOBS_TTL: float = 120.0  # seconds — KV bucket entry TTL
ACTIVE_JOBS_REFRESH: float = 30.0  # seconds — recommended client refresh cadence

_SINGLETON_MODES = frozenset({"steer", "queue"})


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _job_key(job_id: str) -> str:
    return f"job.{kv_safe_part(job_id)}"


def _idx_key(pool_id: str) -> str:
    return f"idx.{kv_safe_part(pool_id)}"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _entry_to_bytes(entry: ActiveJobEntry) -> bytes:
    return json.dumps(
        {
            "job_id": entry.job_id,
            "pool_id": entry.pool_id,
            "status": entry.status,
            "started_at": entry.started_at.isoformat(),
            "steer_subject": entry.steer_subject,
            "concurrency_mode": entry.concurrency_mode,
            "worker_loc": entry.worker_loc,
        }
    ).encode()


def _bytes_to_entry(raw: bytes) -> ActiveJobEntry:
    data = json.loads(raw.decode())
    return ActiveJobEntry(
        job_id=data["job_id"],
        pool_id=data["pool_id"],
        status=data["status"],
        started_at=datetime.datetime.fromisoformat(data["started_at"]),
        steer_subject=data["steer_subject"],
        concurrency_mode=data["concurrency_mode"],
        worker_loc=data.get("worker_loc"),
    )


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def ensure_active_jobs_kv(js: "JetStreamContext") -> "KeyValue":
    """Create or bind the ``factory-active-jobs`` KV bucket idempotently.

    Race-safe: if two processes call this concurrently the ``BadRequestError``
    (err_code 10058 — "stream name already in use") path falls back to a plain
    ``key_value()`` open.

    ``allow_direct=False`` forces readers through ``$JS.API.STREAM.MSG.GET``
    (zero-ACL path per ADR-072 / #1572).

    Fail-fast: any other NATS error propagates immediately — hub StartupSec
    recovers.
    """
    cfg = KeyValueConfig(
        bucket=ACTIVE_JOBS_BUCKET,
        ttl=ACTIVE_JOBS_TTL,
        storage=StorageType.FILE,
        direct=False,  # pinned day-1: readers ride $JS.API.STREAM.MSG.GET (#1572,
        # zero-ACL); allow_direct=True would open an unmodeled $JS.DIRECT.> surface.
    )
    # Try to open an existing bucket first (hot path on restart).
    try:
        kv = await js.key_value(ACTIVE_JOBS_BUCKET)
        log.debug("active-jobs: bound existing KV bucket %s", ACTIVE_JOBS_BUCKET)
        return kv
    except BucketNotFoundError:
        pass

    # Cold-boot: attempt to create.
    try:
        kv = await js.create_key_value(cfg)
        log.info(
            "active-jobs: KV bucket %s created (ttl=%.0f s, allow_direct=False)",
            ACTIVE_JOBS_BUCKET,
            ACTIVE_JOBS_TTL,
        )
        return kv
    except BadRequestError as exc:
        # err_code 10058 = stream name already in use — lost the creation race.
        if exc.err_code != 10058:
            raise
        log.debug("active-jobs: creation race lost, binding %s", ACTIVE_JOBS_BUCKET)
        return await js.key_value(ACTIVE_JOBS_BUCKET)


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


class KvActiveJobsStore:
    """NATS KV implementation of ``ActiveJobsPort``.

    Lifecycle: instantiate with a JetStream context, then call
    ``ensure_active_jobs_kv(js)`` to provision the bucket; bind via
    ``connect()`` before calling any CRUD method.  Typical hub bootstrap::

        kv = await ensure_active_jobs_kv(js)
        store = KvActiveJobsStore(js)
        await store.connect()
    """

    def __init__(self, js: "JetStreamContext") -> None:
        self._js = js
        self._kv: "KeyValue | None" = None

    async def connect(self) -> None:
        """Bind to the pre-provisioned KV bucket."""
        self._kv = await self._js.key_value(ACTIVE_JOBS_BUCKET)

    async def close_store(self) -> None:
        """Release the KV handle (NATS connection managed externally)."""
        self._kv = None

    # ------------------------------------------------------------------
    # ActiveJobsPort
    # ------------------------------------------------------------------

    async def open(self, entry: ActiveJobEntry) -> None:
        """Register *entry* as the active job for its pool.

        For steer/queue modes: acquires the singleton index via CAS.
        For parallel mode: writes the job key only.

        Raises
        ------
        RegistryConflictError
            If another non-expired job already holds the index for this pool
            (steer/queue modes only).
        """
        kv = self._require_kv()
        job_bytes = _entry_to_bytes(entry)
        await kv.put(_job_key(entry.job_id), job_bytes)

        if entry.concurrency_mode not in _SINGLETON_MODES:
            return

        idx_key = _idx_key(entry.pool_id)
        idx_val = entry.job_id.encode()

        # Singleton index via create-only CAS. ``kv.create`` succeeds iff the
        # index key is absent — i.e. no non-expired job holds this pool (a
        # TTL-reaped or explicitly-deleted key is treated as free; nats-py
        # transparently recreates over a tombstone). A live key surfaces as
        # KeyWrongLastSequenceError, which maps to RegistryConflictError.
        # Re-open by the same job_id is idempotent (no steal of a foreign pool).
        try:
            await kv.create(idx_key, idx_val)
        except KeyWrongLastSequenceError:
            winner_id = "<unknown>"
            try:
                winner = await kv.get(idx_key)
                if winner.value:
                    winner_id = winner.value.decode()
            except (KeyNotFoundError, NotFoundError):
                pass
            if winner_id == entry.job_id:
                return  # idempotent re-open by the owning job
            raise RegistryConflictError(entry.pool_id, winner_id)

    async def refresh(self, job_id: str) -> None:
        """Extend the TTL of *job_id* (and its index entry, if any).

        No-op if the job is not found — the job may have already closed or
        expired naturally.
        """
        kv = self._require_kv()
        try:
            existing_entry_raw = await kv.get(_job_key(job_id))
        except (KeyNotFoundError, NotFoundError):
            return

        if not existing_entry_raw.value:
            return

        entry = _bytes_to_entry(existing_entry_raw.value)
        job_bytes = _entry_to_bytes(entry)
        await kv.put(_job_key(job_id), job_bytes)

        if entry.concurrency_mode not in _SINGLETON_MODES:
            return

        # Also re-put the index key to reset its TTL.
        try:
            await kv.put(_idx_key(entry.pool_id), job_id.encode())
        except nats.errors.Error:
            log.warning(
                "active-jobs: refresh: failed to re-put idx key for pool %r",
                entry.pool_id,
            )

    async def close(self, job_id: str) -> None:
        """Remove *job_id* from the registry.

        Deletes the per-job key and, if this job still owns the pool index,
        the index key too.  No-op if the job is not found.
        """
        kv = self._require_kv()
        try:
            existing_raw = await kv.get(_job_key(job_id))
        except (KeyNotFoundError, NotFoundError):
            return

        if existing_raw.value:
            entry = _bytes_to_entry(existing_raw.value)
            # Remove pool→job index only if we still own it.
            if entry.concurrency_mode in _SINGLETON_MODES:
                idx_key = _idx_key(entry.pool_id)
                try:
                    idx_entry = await kv.get(idx_key)
                    current_owner = idx_entry.value.decode() if idx_entry.value else ""
                    if current_owner == job_id:
                        await kv.delete(idx_key)
                except (KeyNotFoundError, NotFoundError):
                    pass

        await kv.delete(_job_key(job_id))

    async def get_by_pool(self, pool_id: str) -> ActiveJobEntry | None:
        """Return the active job for *pool_id*, or ``None`` if absent."""
        kv = self._require_kv()
        try:
            idx_entry = await kv.get(_idx_key(pool_id))
        except (KeyNotFoundError, NotFoundError):
            return None

        if not idx_entry.value:
            return None

        job_id = idx_entry.value.decode()
        try:
            job_raw = await kv.get(_job_key(job_id))
        except (KeyNotFoundError, NotFoundError):
            return None

        if not job_raw.value:
            return None

        return _bytes_to_entry(job_raw.value)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_kv(self) -> "KeyValue":
        if self._kv is None:
            raise RuntimeError("KvActiveJobsStore not connected — call connect() first")
        return self._kv
