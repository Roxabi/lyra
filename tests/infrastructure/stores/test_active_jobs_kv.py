"""Tests for KvActiveJobsStore and ensure_active_jobs_kv (#1796)."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from nats.js.errors import (
    BadRequestError,
    BucketNotFoundError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)

from factory.core.ports.active_jobs import ActiveJobEntry, RegistryConflictError
from factory.infrastructure.stores.active_jobs_kv import (
    ACTIVE_JOBS_BUCKET,
    ACTIVE_JOBS_TTL,
    KvActiveJobsStore,
    _idx_key,
    _job_key,
    ensure_active_jobs_kv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _entry(
    job_id: str = "job-1",
    pool_id: str = "pool.tg.main",
    concurrency_mode: str = "steer",
) -> ActiveJobEntry:
    return ActiveJobEntry(
        job_id=job_id,
        pool_id=pool_id,
        status="open",
        started_at=_TS,
        steer_subject="factory.steer.pool.tg.main",
        concurrency_mode=concurrency_mode,
        worker_loc=None,
    )


def _kv_entry(value: bytes, revision: int = 1) -> MagicMock:
    e = MagicMock()
    e.value = value
    e.revision = revision
    return e


# ---------------------------------------------------------------------------
# KvActiveJobsStore — open()
# ---------------------------------------------------------------------------


class TestOpen:
    async def test_open_writes_job_and_idx_steer(self):
        """steer mode: puts job key + acquires singleton index."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError  # idx absent on first open
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="steer")
        await store.open(entry)

        kv.put.assert_awaited_once_with(_job_key("job-1"), kv.put.await_args.args[1])
        kv.create.assert_awaited_once_with(_idx_key("pool.tg.main"), b"job-1")

    async def test_open_writes_job_and_idx_queue(self):
        """queue mode also acquires singleton index."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="queue")
        await store.open(entry)

        kv.create.assert_awaited_once_with(_idx_key("pool.tg.main"), b"job-1")

    async def test_open_writes_job_no_idx_parallel(self):
        """parallel mode: writes job key only — no index touch."""
        kv = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="parallel")
        await store.open(entry)

        kv.put.assert_awaited_once()
        kv.get.assert_not_awaited()
        kv.create.assert_not_awaited()

    async def test_open_colon_pool_id_sanitization(self):
        """Colons in pool_id are replaced with underscores in KV keys."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(pool_id="pool:tg:main", concurrency_mode="steer")
        await store.open(entry)

        idx_key_used = kv.create.await_args.args[0]
        assert ":" not in idx_key_used
        assert idx_key_used == _idx_key("pool:tg:main")

    async def test_open_update_existing_idx(self):
        """When idx exists, open() updates it via CAS."""
        kv = AsyncMock()
        existing = _kv_entry(b"old-job", revision=5)
        kv.get.return_value = existing  # idx found
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="steer")
        await store.open(entry)

        kv.update.assert_awaited_once_with(
            _idx_key("pool.tg.main"), b"job-1", last_revision=5
        )
        kv.create.assert_not_awaited()

    async def test_open_conflict_on_update_raises(self):
        """CAS collision on update → RegistryConflictError."""
        kv = AsyncMock()
        existing = _kv_entry(b"other-job", revision=3)
        kv.get.side_effect = [
            existing,  # first get (idx exists)
            _kv_entry(b"other-job", revision=4),  # winner read after CAS fail
        ]
        kv.update.side_effect = KeyWrongLastSequenceError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="steer")
        with pytest.raises(RegistryConflictError) as exc_info:
            await store.open(entry)

        assert exc_info.value.pool_id == "pool.tg.main"
        assert exc_info.value.existing_job_id == "other-job"

    async def test_open_conflict_on_create_raises(self):
        """CAS collision on create (idx absent → race) → RegistryConflictError."""
        kv = AsyncMock()
        kv.get.side_effect = [
            KeyNotFoundError,  # idx absent (triggers create path)
            _kv_entry(b"winner-job"),  # winner read after create CAS fail
        ]
        kv.create.side_effect = KeyWrongLastSequenceError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        entry = _entry(concurrency_mode="steer")
        with pytest.raises(RegistryConflictError) as exc_info:
            await store.open(entry)

        assert exc_info.value.existing_job_id == "winner-job"


# ---------------------------------------------------------------------------
# KvActiveJobsStore — refresh()
# ---------------------------------------------------------------------------


class TestRefresh:
    async def test_refresh_reputs_job_and_idx(self):
        """refresh() re-puts the job key and re-puts the index key (steer)."""
        kv = AsyncMock()
        entry = _entry(concurrency_mode="steer")
        import json

        raw = json.dumps(
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
        kv.get.return_value = _kv_entry(raw)
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.refresh("job-1")

        assert kv.put.await_count == 2
        put_keys = [c.args[0] for c in kv.put.await_args_list]
        assert _job_key("job-1") in put_keys
        assert _idx_key("pool.tg.main") in put_keys

    async def test_refresh_noop_unknown_job(self):
        """refresh() is a no-op when the job is not found."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.refresh("nonexistent-job")

        kv.put.assert_not_awaited()

    async def test_refresh_no_idx_for_parallel(self):
        """refresh() skips index re-put for parallel mode."""
        kv = AsyncMock()
        entry = _entry(concurrency_mode="parallel")
        import json

        raw = json.dumps(
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
        kv.get.return_value = _kv_entry(raw)
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.refresh("job-1")

        assert kv.put.await_count == 1
        assert kv.put.await_args.args[0] == _job_key("job-1")


# ---------------------------------------------------------------------------
# KvActiveJobsStore — close()
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_deletes_job_and_idx_when_owner(self):
        """close() removes both job key and idx key when this job owns the idx."""
        kv = AsyncMock()
        entry = _entry(concurrency_mode="steer")
        import json

        raw = json.dumps(
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
        # job get → job raw; idx get → current owner = "job-1" (same)
        kv.get.side_effect = [_kv_entry(raw), _kv_entry(b"job-1")]
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.close("job-1")

        deleted_keys = [c.args[0] for c in kv.delete.await_args_list]
        assert _idx_key("pool.tg.main") in deleted_keys
        assert _job_key("job-1") in deleted_keys

    async def test_close_skips_idx_if_not_owner(self):
        """close() keeps idx when another job has taken ownership."""
        kv = AsyncMock()
        entry = _entry(concurrency_mode="steer")
        import json

        raw = json.dumps(
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
        # idx owner = different job
        kv.get.side_effect = [_kv_entry(raw), _kv_entry(b"other-job")]
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.close("job-1")

        deleted_keys = [c.args[0] for c in kv.delete.await_args_list]
        assert _idx_key("pool.tg.main") not in deleted_keys
        assert _job_key("job-1") in deleted_keys

    async def test_close_noop_unknown_job(self):
        """close() is a no-op when the job is not found."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        await store.close("nonexistent-job")

        kv.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# KvActiveJobsStore — get_by_pool()
# ---------------------------------------------------------------------------


class TestGetByPool:
    async def test_get_by_pool_returns_entry(self):
        """Happy path: idx→job→deserialized entry."""
        kv = AsyncMock()
        entry = _entry(concurrency_mode="steer")
        import json

        raw = json.dumps(
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
        kv.get.side_effect = [_kv_entry(b"job-1"), _kv_entry(raw)]
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        result = await store.get_by_pool("pool.tg.main")

        assert result is not None
        assert result.job_id == "job-1"
        assert result.pool_id == "pool.tg.main"

    async def test_get_by_pool_returns_none_absent_pool(self):
        """Returns None when no idx entry exists for the pool."""
        kv = AsyncMock()
        kv.get.side_effect = KeyNotFoundError
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        result = await store.get_by_pool("pool.tg.missing")

        assert result is None

    async def test_get_by_pool_returns_none_when_job_key_missing(self):
        """Returns None when idx points to a job that has already expired."""
        kv = AsyncMock()
        kv.get.side_effect = [_kv_entry(b"job-expired"), KeyNotFoundError]
        js = AsyncMock()
        js.key_value.return_value = kv
        store = KvActiveJobsStore(js)
        await store.connect()

        result = await store.get_by_pool("pool.tg.main")

        assert result is None


# ---------------------------------------------------------------------------
# ensure_active_jobs_kv — provisioning paths
# ---------------------------------------------------------------------------


class TestEnsureActiveJobsKv:
    async def test_hot_path_binds_existing_bucket(self):
        """key_value() succeeds on first call — returns existing bucket handle."""
        kv_existing = AsyncMock()
        js = AsyncMock()
        js.key_value.return_value = kv_existing

        result = await ensure_active_jobs_kv(js)

        assert result is kv_existing
        js.key_value.assert_awaited_once_with(ACTIVE_JOBS_BUCKET)
        js.create_key_value.assert_not_awaited()

    async def test_cold_boot_creates_bucket(self):
        """BucketNotFoundError → create_key_value() cold-boot path."""
        kv_created = AsyncMock()
        js = AsyncMock()
        js.key_value.side_effect = BucketNotFoundError
        js.create_key_value.return_value = kv_created

        result = await ensure_active_jobs_kv(js)

        assert result is kv_created
        js.create_key_value.assert_awaited_once()

    async def test_cold_boot_race_lost_binds_existing(self):
        """create_key_value → BadRequestError(10058) → fallback key_value()."""
        kv_fallback = AsyncMock()
        js = AsyncMock()
        js.key_value.side_effect = [BucketNotFoundError, kv_fallback]

        bad_req = BadRequestError()
        bad_req.err_code = 10058
        js.create_key_value.side_effect = bad_req

        result = await ensure_active_jobs_kv(js)

        assert result is kv_fallback
        assert js.key_value.await_count == 2

    async def test_cold_boot_unexpected_bad_request_propagates(self):
        """BadRequestError with err_code != 10058 propagates (not swallowed)."""
        js = AsyncMock()
        js.key_value.side_effect = BucketNotFoundError

        bad_req = BadRequestError()
        bad_req.err_code = 9999
        js.create_key_value.side_effect = bad_req

        with pytest.raises(BadRequestError):
            await ensure_active_jobs_kv(js)

    async def test_boot_ttl_passed_to_create_key_value(self):
        """KeyValueConfig passed to create_key_value has ttl=ACTIVE_JOBS_TTL."""
        js = AsyncMock()
        js.key_value.side_effect = BucketNotFoundError
        js.create_key_value.return_value = AsyncMock()

        await ensure_active_jobs_kv(js)

        cfg = js.create_key_value.await_args.args[0]
        assert cfg.ttl == ACTIVE_JOBS_TTL
        assert cfg.bucket == ACTIVE_JOBS_BUCKET

    async def test_boot_allow_direct_false(self):
        """KeyValueConfig does not set allow_direct (defaults to False for STREAM.MSG.GET path)."""
        js = AsyncMock()
        js.key_value.side_effect = BucketNotFoundError
        js.create_key_value.return_value = AsyncMock()

        await ensure_active_jobs_kv(js)

        cfg = js.create_key_value.await_args.args[0]
        # allow_direct defaults to False — check it was not set to True
        assert not getattr(cfg, "allow_direct", False)


# ---------------------------------------------------------------------------
# Guard: not-connected raises RuntimeError
# ---------------------------------------------------------------------------


class TestNotConnectedGuard:
    async def test_crud_before_connect_raises(self):
        js = AsyncMock()
        store = KvActiveJobsStore(js)

        entry = _entry()
        with pytest.raises(RuntimeError, match="connect"):
            await store.open(entry)
        with pytest.raises(RuntimeError, match="connect"):
            await store.refresh("job-1")
        with pytest.raises(RuntimeError, match="connect"):
            await store.close("job-1")
        with pytest.raises(RuntimeError, match="connect"):
            await store.get_by_pool("pool.tg.main")
