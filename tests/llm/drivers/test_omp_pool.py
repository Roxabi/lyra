"""Tests for OmpPool (Model B) — flat free-set of warm (RpcClient, RpcBridge) workers.

Covers acquire/release semantics, switch_session vs new_session+get_state, the
semaphore concurrency cap, aclose stopping all workers, and no_session=False.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.adapters.omp._rpc_bridge import (
    _DEFAULT_MODEL,
    _DEFAULT_REQUEST_TIMEOUT,
    _ENV_REQUEST_TIMEOUT_KEY,
    _read_request_timeout,
)
from factory.adapters.omp.omp_pool import (
    _DEFAULT_CAP,
    OmpPool,
    _PoolWorker,
    _read_cap,
)

# ---------------------------------------------------------------------------
# Fake seam helpers
# ---------------------------------------------------------------------------

_FAKE_SESSION_FILE = "/tmp/fake_session.jsonl"


def _mock_rpc_client(session_file: str = _FAKE_SESSION_FILE) -> MagicMock:
    """Return a MagicMock whose blocking RpcClient methods record calls."""
    client = MagicMock()
    client.start.return_value = None
    client.new_session.return_value = MagicMock()  # CancellationResult, NOT a path
    client.switch_session.return_value = None
    client.get_state.return_value = SimpleNamespace(session_file=session_file)
    client.stop.return_value = None
    return client


def _mock_bridge() -> MagicMock:
    """Return a MagicMock for RpcBridge; attach is an AsyncMock."""
    bridge = MagicMock()
    bridge.attach = AsyncMock(return_value=None)
    return bridge


def _patch_start_worker(pool: OmpPool, client: MagicMock, bridge: MagicMock) -> None:
    """Patch _start_worker so pool tests don't need a real binary or omp_rpc import.

    We replace pool._start_worker with an async function that:
      - Creates a _PoolWorker(client=<fake>, bridge=<fake>)
      - Appends it to pool._all  (same as the real impl)
      - Does NOT touch _verify_digest or omp_rpc
    """

    async def _fake_start_worker() -> _PoolWorker:
        worker = _PoolWorker(client=client, bridge=bridge)
        pool._all.append(worker)
        return worker

    pool._start_worker = _fake_start_worker  # type: ignore[method-assign]


def _make_pool(cap: int = 4) -> tuple[OmpPool, list[MagicMock], list[MagicMock]]:
    """Return a pool with _start_worker patched, plus separate client/bridge lists.

    Each call to _start_worker pops from the fronts of the two lists; if exhausted
    it creates fresh fakes, giving tests that start multiple workers full control.
    """
    clients: list[MagicMock] = []
    bridges: list[MagicMock] = []

    pool = OmpPool(omp_bin=Path("/fake/omp"), provider="litellm", model="grok-4-fast")
    # Force cap via a new semaphore (avoids env var interaction at construction time)
    pool._sem = asyncio.Semaphore(cap)

    async def _multi_start_worker() -> _PoolWorker:
        client = _mock_rpc_client()
        bridge = _mock_bridge()
        clients.append(client)
        bridges.append(bridge)
        worker = _PoolWorker(client=client, bridge=bridge)
        pool._all.append(worker)
        return worker

    pool._start_worker = _multi_start_worker  # type: ignore[method-assign]
    return pool, clients, bridges


# ---------------------------------------------------------------------------
# Fake nc for register()
# ---------------------------------------------------------------------------

_NC = MagicMock()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpPool:
    # -- 1. acquire returns _PoolWorker -------------------------------------

    @pytest.mark.asyncio
    async def test_acquire_returns_poolworker(self) -> None:
        """acquire(None) returns a _PoolWorker with client+bridge set."""
        pool, clients, bridges = _make_pool()
        await pool.register(_NC)

        worker = await pool.acquire(None)

        assert isinstance(worker, _PoolWorker)
        assert worker.client is clients[0]
        assert worker.bridge is bridges[0]

        await pool.aclose()

    # -- 2. acquire with session_file → switch_session ----------------------

    @pytest.mark.asyncio
    async def test_acquire_with_token_calls_switch_session(self) -> None:
        """acquire(token) calls switch_session(token); new_session not called."""
        pool, clients, _ = _make_pool()
        await pool.register(_NC)
        token = "/path/x.jsonl"

        worker = await pool.acquire(token)

        clients[0].switch_session.assert_called_once_with(token)
        assert worker.session_file == token
        clients[0].new_session.assert_not_called()

        await pool.aclose()

    # -- 3. acquire without token → new_session + get_state -----------------

    @pytest.mark.asyncio
    async def test_acquire_no_token_calls_new_session_and_get_state(self) -> None:
        """acquire(None) calls new_session()+get_state(); stores session_file."""
        pool, clients, _ = _make_pool()
        await pool.register(_NC)

        worker = await pool.acquire(None)

        clients[0].new_session.assert_called_once()
        clients[0].get_state.assert_called_once()
        clients[0].switch_session.assert_not_called()
        assert worker.session_file == _FAKE_SESSION_FILE

        await pool.aclose()

    # -- 4. release returns worker to free list -----------------------------

    @pytest.mark.asyncio
    async def test_release_returns_worker_to_free_list(self) -> None:
        """release(w) → _free; next acquire returns the SAME object."""
        pool, clients, _ = _make_pool()
        await pool.register(_NC)

        first = await pool.acquire(None)
        pool.release(first)

        # Second acquire should pop first from _free — no new _start_worker call
        second = await pool.acquire(None)

        assert second is first, "warm hit must return the same _PoolWorker"
        assert len(clients) == 1, "only one worker should have been constructed"

        await pool.aclose()

    # -- 5. semaphore bounds concurrency to cap -----------------------------

    @pytest.mark.asyncio
    async def test_semaphore_bounds_concurrency_to_cap(self) -> None:
        """Cap=2: two acquires succeed; third blocks until a release opens a slot."""
        pool, _, _ = _make_pool(cap=2)
        await pool.register(_NC)

        w1 = await pool.acquire(None)
        w2 = await pool.acquire(None)

        # Third acquire must not complete while sem is exhausted
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pool.acquire(None), timeout=0.05)

        # After releasing one slot the pending acquire can complete
        pool.release(w1)
        w3 = await asyncio.wait_for(pool.acquire(None), timeout=1.0)

        # w3 is the worker we just released (warm hit from _free)
        assert w3 is w1

        pool.release(w2)
        pool.release(w3)
        await pool.aclose()

    # -- 6. aclose stops all workers ----------------------------------------

    @pytest.mark.asyncio
    async def test_aclose_stops_all_workers(self) -> None:
        """aclose() stops every worker (incl. un-released); _free/_all cleared."""
        pool, clients, _ = _make_pool()
        await pool.register(_NC)

        await pool.acquire(None)  # first worker stays checked out
        w2 = await pool.acquire(None)
        pool.release(w2)  # w2 returns to _free; the first stays checked out

        await pool.aclose()

        for client in clients:
            client.stop.assert_called_once()
        assert pool._free == []
        assert pool._all == []

    # -- 7. _start_worker constructs RpcClient with no_session=False --------

    @pytest.mark.asyncio
    async def test_start_worker_constructs_client_no_session_false(self) -> None:
        """_start_worker passes no_session=False to omp_rpc.RpcClient.

        Strategy: inject a capturing fake into sys.modules["omp_rpc"] so the
        deferred ``import omp_rpc`` inside _start_worker picks it up, then patch
        _verify_digest + RpcBridge so no real binary is needed.
        """
        import sys

        pool = OmpPool(omp_bin=Path("/fake/omp"), provider="litellm", model=None)
        await pool.register(_NC)

        captured_kwargs: dict = {}
        fake_bridge = _mock_bridge()

        class _CapturingFakeOmpRpc:
            def RpcClient(self, **kwargs: object) -> MagicMock:  # noqa: N802
                captured_kwargs.update(kwargs)
                return _mock_rpc_client()

        sys.modules["omp_rpc"] = _CapturingFakeOmpRpc()  # type: ignore[assignment]
        try:
            with (
                patch(
                    "factory.adapters.omp._rpc_bridge._verify_digest",
                    return_value=None,
                ),
                patch(
                    "factory.adapters.omp._rpc_bridge.RpcBridge",
                    return_value=fake_bridge,
                ),
            ):
                await pool.acquire(None)
        finally:
            sys.modules.pop("omp_rpc", None)

        assert captured_kwargs.get("no_session") is False, (
            f"Expected no_session=False, got kwargs={captured_kwargs}"
        )
        assert captured_kwargs.get("model") == _DEFAULT_MODEL
        assert captured_kwargs.get("request_timeout") == _DEFAULT_REQUEST_TIMEOUT

        await pool.aclose()

    @pytest.mark.asyncio
    async def test_start_worker_defaults_model_when_unset(self) -> None:
        """OmpPool(model=None) pins the RpcBridge default on RpcClient (#1910)."""
        import sys

        pool = OmpPool(omp_bin=Path("/fake/omp"))
        await pool.register(_NC)

        captured_kwargs: dict = {}
        fake_bridge = _mock_bridge()

        class _CapturingFakeOmpRpc:
            def RpcClient(self, **kwargs: object) -> MagicMock:  # noqa: N802
                captured_kwargs.update(kwargs)
                return _mock_rpc_client()

        sys.modules["omp_rpc"] = _CapturingFakeOmpRpc()  # type: ignore[assignment]
        try:
            with (
                patch(
                    "factory.adapters.omp._rpc_bridge._verify_digest",
                    return_value=None,
                ),
                patch(
                    "factory.adapters.omp._rpc_bridge.RpcBridge",
                    return_value=fake_bridge,
                ),
            ):
                await pool.acquire(None)
        finally:
            sys.modules.pop("omp_rpc", None)

        assert captured_kwargs.get("model") == _DEFAULT_MODEL
        await pool.aclose()

    # -- Bonus: _read_cap falls back to default -----------------------------

    def test_read_cap_defaults_to_four(self) -> None:
        """_read_cap() returns _DEFAULT_CAP when OMP_POOL_CAP is unset."""
        env_without_cap = {k: v for k, v in os.environ.items() if k != "OMP_POOL_CAP"}
        with patch.dict(os.environ, env_without_cap, clear=True):
            assert _read_cap() == _DEFAULT_CAP
            assert _DEFAULT_CAP == 4

    def test_read_cap_honours_env_var(self) -> None:
        """_read_cap() returns the integer value of OMP_POOL_CAP when set."""
        with patch.dict(os.environ, {"OMP_POOL_CAP": "7"}):
            assert _read_cap() == 7

    def test_read_request_timeout_honours_env_var(self) -> None:
        with patch.dict(os.environ, {_ENV_REQUEST_TIMEOUT_KEY: "90"}):
            assert _read_request_timeout() == 90.0
