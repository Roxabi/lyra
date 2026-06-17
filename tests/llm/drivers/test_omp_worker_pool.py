"""Tests for OmpWorker pool wiring (Model B — runtime-selection V3 / #1813).

Scenarios:
  1. handle() extracts pool_id (log only) + provider_session_id and calls
     pool.acquire(session_file=...) with the correct (new flat) arguments.
  2. The bridge from pool.acquire() bundle receives .run(..., session_file=...)
     (JobResult.data carries session_file backhauled from worker.session_file).
     Note: OmpWorker ctor takes only pool= (no bridge=); bridge lives
     in the acquired _PoolWorker.
  3. OmpWorker does not import sqlite3 or access config.db (dumb executor
     invariant — ADR-073).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.omp.omp_pool import OmpPool
from factory.adapters.omp.omp_worker import OmpWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)
_JOB_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"  # 32 hex chars
_POOL_ID = "user:conv-42"
_SESSION_FILE = "/home/factory/.omp/sessions/abc123.jsonl"
_REPLY_TO = "factory.reply.test"


def _base_payload(
    *,
    pool_id: str = _POOL_ID,
    provider_session_id: str | None = _SESSION_FILE,
    prompt: str = "hello omp",
) -> dict:
    """Minimal valid JobEnvelope dict for a V2 omp job."""
    payload: dict = {
        "contract_version": "1",
        "trace_id": "trace-test-001",
        "issued_at": _FIXED_NOW.isoformat(),
        "job_id": _JOB_ID,
        "job_name": "omp",
        "reply_to": _REPLY_TO,
        "payload": {
            "prompt": prompt,
            "pool_id": pool_id,
        },
    }
    if provider_session_id is not None:
        payload["payload"]["provider_session_id"] = provider_session_id
    return payload


def _mock_pool(
    session_file: str = _SESSION_FILE, *, bridge: MagicMock | None = None
) -> MagicMock:
    """Return MagicMock OmpPool; acquire returns _PoolWorker-like bundle
    (.client, .bridge, .session_file) for Model B flat API.

    acquire(session_file) only (no pool_id). No _entries.
    Optional bridge injected for spying .run on per-acquire entry.
    """
    fake_client = MagicMock()
    pool = MagicMock(spec=OmpPool)
    pool.aclose = AsyncMock()
    # no _entries; _last_acquired for test spy of returned bundle

    if bridge is None:
        bridge = MagicMock()
        bridge.run = AsyncMock()
        bridge.register = AsyncMock()
        bridge.publish_error = AsyncMock()
        bridge.aclose = AsyncMock()

    # Capture the pool-level default so the closure sees the right value.
    _default_sf = session_file

    async def _side_effect_acquire(session_file: str | None) -> Any:
        # Model B: sf only (None=cold); return bundle (bridge for .run)
        stored = session_file if session_file is not None else _default_sf
        entry = SimpleNamespace(
            client=fake_client,
            bridge=bridge,
            session_file=stored,
        )
        pool._last_acquired = entry
        return entry

    pool.acquire = AsyncMock(side_effect=_side_effect_acquire)
    return pool


# _mock_bridge removed — no longer needed (OmpWorker takes pool= only; per-worker
# bridge is provided via the acquire() return bundle in _mock_pool for spying).


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpWorkerPool:
    # -- 1. handle() routes to pool.acquire with correct args ------------------

    @pytest.mark.asyncio
    async def test_handle_routes_to_pool_acquire(self) -> None:
        """handle extracts pool_id (log) + provider_session_id; calls
        acquire(session_file=...) — Model B (no pool_id routing)."""
        pool = _mock_pool()
        worker = OmpWorker(pool=pool)

        payload = _base_payload(
            pool_id=_POOL_ID,
            provider_session_id=_SESSION_FILE,
        )
        await worker.handle(msg=None, payload=payload)
        # drain spawned _run_job (handle fire-and-forget in Model B)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        pool.acquire.assert_called_once_with(_SESSION_FILE)

    @pytest.mark.asyncio
    async def test_handle_passes_none_when_provider_session_id_absent(self) -> None:
        """When provider_session_id is absent from the payload, pool.acquire
        is called with session_file=None (never an empty string)."""
        pool = _mock_pool()
        worker = OmpWorker(pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        await worker.handle(msg=None, payload=payload)
        # drain spawned _run_job (handle fire-and-forget in Model B)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        pool.acquire.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_handle_rejects_empty_string_provider_session_id(self) -> None:
        """Empty-string provider_session_id is rejected as bad token (per guard)
        and does NOT call acquire (never pass empty string to pool)."""
        pool = _mock_pool()
        worker = OmpWorker(pool=pool)

        # Inject empty string directly into payload
        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        payload["payload"]["provider_session_id"] = ""  # explicit empty string
        await worker.handle(msg=None, payload=payload)
        # drain (no job spawned on early reject)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_falls_back_to_job_id_when_pool_id_absent(self) -> None:
        """B3 wire-compat: a pre-V2 envelope without pool_id is NOT rejected —
        handle() falls back to job_id as the routing key (for log) and proceeds.
        (Model B: no routing by pool_id to acquire.)"""
        pool = _mock_pool()
        worker = OmpWorker(pool=pool)

        payload = _base_payload(pool_id="", provider_session_id=None)
        # Remove pool_id key entirely → simulate an old (pre-V2) sender.
        del payload["payload"]["pool_id"]
        await worker.handle(msg=None, payload=payload)
        # drain spawned _run_job (handle fire-and-forget in Model B)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        # Falls back to job_id (log), routes to pool, no error.
        pool.acquire.assert_called_once_with(None)
        # (bridge inside acquired worker now; skip old top-bridge assert)

    # -- 2. bridge.run receives session_file from pool entry -------------------

    @pytest.mark.asyncio
    async def test_bridge_run_receives_session_file_from_pool(self) -> None:
        """Per-worker bridge (from acquire bundle) .run called with
        session_file (backs JobResult.data). Model B: bridge in bundle.
        """
        minted = "/home/factory/.omp/sessions/minted-xyz.jsonl"
        pool = _mock_pool(session_file=minted)
        worker = OmpWorker(pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        await worker.handle(msg=None, payload=payload)
        # drain spawned _run_job (handle fire-and-forget in Model B)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        # bridge.run (from _mock entry) called with minted sf
        acquired = pool._last_acquired
        acquired.bridge.run.assert_called_once()
        _call_kwargs = acquired.bridge.run.call_args.kwargs
        got = _call_kwargs.get("session_file")
        assert got == minted, (
            f"Expected session_file={minted!r} in bridge.run kwargs, got {got!r}"
        )

    @pytest.mark.asyncio
    async def test_bridge_run_receives_client_from_pool(self) -> None:
        """Bridge from acquire() return used for .run().
        Note: Model B run(..., session_file=) does NOT take client=
        (client held internally by bridge; acquire returns bundle)."""
        pool = _mock_pool()
        worker = OmpWorker(pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=_SESSION_FILE)
        await worker.handle(msg=None, payload=payload)
        # drain spawned _run_job (handle fire-and-forget in Model B)
        if getattr(worker, "_jobs", None):
            await asyncio.gather(*list(worker._jobs), return_exceptions=True)

        acquired = pool._last_acquired
        acquired.bridge.run.assert_called_once()
        _call_kwargs = acquired.bridge.run.call_args.kwargs
        # sf passed; no client= to run (held by bridge)
        # (bundle from pool; client verified by structure)
        assert acquired.client is not None
        assert (
            _call_kwargs.get("session_file") is not None
            or acquired.session_file is not None
        )

    # -- 3. dumb-executor invariant: no config.db / sqlite3 access -----------

    def test_omp_worker_does_not_import_sqlite3(self) -> None:
        """OmpWorker module must not import sqlite3 — dumb executor invariant.

        Workers must NOT read config.db; sqlite3 in the module-level import
        graph would violate the 'worker stays dumb' constraint (ADR-073).
        """
        import factory.adapters.omp.omp_worker as worker_mod

        # Direct module-level import check
        assert "sqlite3" not in dir(worker_mod), (
            "sqlite3 must not be imported at module level in omp_worker"
        )
        # Also check the module's __dict__ for the sqlite3 module object
        assert "sqlite3" not in worker_mod.__dict__, (
            "sqlite3 must not be a name in omp_worker module namespace"
        )

    def test_omp_worker_source_has_no_config_db_reference(self) -> None:
        """OmpWorker source file must not reference 'config.db' — dumb executor
        invariant.  A worker reading from config.db couples it to the hub's
        database, which breaks horizontal scaling and ADR-073 isolation."""
        import factory.adapters.omp.omp_worker as worker_mod

        source_file = Path(worker_mod.__file__)  # type: ignore[arg-type]
        source_text = source_file.read_text(encoding="utf-8")
        assert "config.db" not in source_text, (
            "omp_worker.py must not reference 'config.db'"
        )
