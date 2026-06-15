"""Tests for OmpWorker pool wiring (T4 — runtime-selection V2).

Three scenarios:
  1. handle() extracts pool_id + provider_session_id and calls pool.acquire
     with the correct arguments.
  2. bridge.run() receives client= and session_file= matching the pool entry
     (i.e. JobResult.data would carry session_file).
  3. OmpWorker does not import sqlite3 or access config.db (dumb executor
     invariant — ADR-073).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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


def _mock_pool(session_file: str = _SESSION_FILE) -> MagicMock:
    """Return a MagicMock OmpPool whose acquire() succeeds and entries readable.

    The ``session_file`` argument is the path that the pool entry will report
    back after acquire() — mimicking what OmpPool stores in _entries[pool_id].
    When the caller passes ``provider_session_id=None`` (no-token cold start),
    the pool mints a new session path; ``session_file`` is that minted path.
    When the caller passes a non-None ``session_file`` kwarg to acquire(), the
    pool stores that exact path on the entry.
    """
    fake_client = MagicMock()
    pool = MagicMock(spec=OmpPool)
    pool.aclose = AsyncMock()
    pool._entries = {}  # start empty; populated below

    # Capture the pool-level default so the closure sees the right value.
    _default_sf = session_file

    async def _side_effect_acquire(pid: str, *, session_file: str | None) -> MagicMock:
        # Mimic OmpPool: if a session_file was provided use it; otherwise use
        # the minted default (the value passed to _mock_pool()).
        stored = session_file if session_file is not None else _default_sf
        pool._entries[pid] = SimpleNamespace(session_file=stored)
        return fake_client

    pool.acquire = AsyncMock(side_effect=_side_effect_acquire)
    return pool


def _mock_bridge() -> MagicMock:
    """Return a MagicMock RpcBridge whose async methods are AsyncMocks."""
    bridge = MagicMock()
    bridge.register = AsyncMock()
    bridge.run = AsyncMock()
    bridge.publish_error = AsyncMock()
    bridge.aclose = AsyncMock()
    return bridge


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpWorkerPool:
    # -- 1. handle() routes to pool.acquire with correct args ------------------

    @pytest.mark.asyncio
    async def test_handle_routes_to_pool_acquire(self) -> None:
        """handle() extracts pool_id and provider_session_id from the envelope
        payload and calls pool.acquire(pool_id, session_file=provider_session_id)."""
        pool = _mock_pool()
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        payload = _base_payload(
            pool_id=_POOL_ID,
            provider_session_id=_SESSION_FILE,
        )
        await worker.handle(msg=None, payload=payload)

        pool.acquire.assert_called_once_with(_POOL_ID, session_file=_SESSION_FILE)

    @pytest.mark.asyncio
    async def test_handle_passes_none_when_provider_session_id_absent(self) -> None:
        """When provider_session_id is absent from the payload, pool.acquire
        is called with session_file=None (never an empty string)."""
        pool = _mock_pool()
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        await worker.handle(msg=None, payload=payload)

        pool.acquire.assert_called_once_with(_POOL_ID, session_file=None)

    @pytest.mark.asyncio
    async def test_handle_passes_none_when_provider_session_id_empty_string(
        self,
    ) -> None:
        """Empty-string provider_session_id is normalised to None before
        being passed to pool.acquire (never pass empty string)."""
        pool = _mock_pool()
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        # Inject empty string directly into payload
        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        payload["payload"]["provider_session_id"] = ""  # explicit empty string
        await worker.handle(msg=None, payload=payload)

        pool.acquire.assert_called_once_with(_POOL_ID, session_file=None)

    @pytest.mark.asyncio
    async def test_handle_falls_back_to_job_id_when_pool_id_absent(self) -> None:
        """B3 wire-compat: a pre-V2 envelope without pool_id is NOT rejected —
        handle() falls back to job_id as the routing key and proceeds."""
        pool = _mock_pool()
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        payload = _base_payload(pool_id="", provider_session_id=None)
        # Remove pool_id key entirely → simulate an old (pre-V2) sender.
        del payload["payload"]["pool_id"]
        await worker.handle(msg=None, payload=payload)

        # Falls back to job_id, routes to the pool, does NOT publish an error.
        pool.acquire.assert_called_once_with(_JOB_ID, session_file=None)
        bridge.publish_error.assert_not_called()

    # -- 2. bridge.run receives session_file from pool entry -------------------

    @pytest.mark.asyncio
    async def test_bridge_run_receives_session_file_from_pool(self) -> None:
        """bridge.run() is called with session_file= matching the pool entry's
        session_file (which backs the session_file key in JobResult.data)."""
        minted = "/home/factory/.omp/sessions/minted-xyz.jsonl"
        pool = _mock_pool(session_file=minted)
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=None)
        await worker.handle(msg=None, payload=payload)

        # bridge.run must have been called with the minted session file
        bridge.run.assert_called_once()
        _call_kwargs = bridge.run.call_args.kwargs
        got = _call_kwargs.get("session_file")
        assert got == minted, (
            f"Expected session_file={minted!r} in bridge.run kwargs, got {got!r}"
        )

    @pytest.mark.asyncio
    async def test_bridge_run_receives_client_from_pool(self) -> None:
        """bridge.run() is called with the client object returned by pool.acquire."""
        pool = _mock_pool()
        bridge = _mock_bridge()
        worker = OmpWorker(bridge=bridge, pool=pool)

        payload = _base_payload(pool_id=_POOL_ID, provider_session_id=_SESSION_FILE)
        await worker.handle(msg=None, payload=payload)

        bridge.run.assert_called_once()
        _call_kwargs = bridge.run.call_args.kwargs
        # Verify bridge.run received a non-None client object from pool.acquire.
        assert "client" in _call_kwargs, "bridge.run must receive client= kwarg"
        assert _call_kwargs["client"] is not None

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
