"""Integration RED-GATE: OmpPool + OmpRpcDriver session resume (#1813).

These tests wire OmpPool (with _start_client patched to a fake RpcClient) together
with OmpRpcDriver (against an AsyncMock NATS connection) and assert the full
cold-start → persist → resume lifecycle without requiring a live omp binary.

They are skipped in CI (INTEGRATION env var not set).  Run locally or on M1 with:

    INTEGRATION=1 uv run --frozen pytest \\
        tests/integration/test_omp_session_resume_integration.py -x

M1 end-to-end validation checklist (requires the live container)
---------------------------------------------------------------
1.  Reproduce start::

        podman run --rm --userns keep-id \\
          --secret factory-nats-omp,type=env,target=NATS_CREDS \\
          --secret factory-litellm-key,type=env,target=LITELLM_API_KEY \\
          --tmpfs /home/factory/.config/omp-pi:mode=1777 \\
          --tmpfs /home/factory/.omp:mode=1777 \\
          ghcr.io/roxabi/factory:staging \\
          factory adapter omp

2.  Wait for "ready" log line (``omp_worker: ready``).

3.  Submit a test job via NATS CLI and assert ``JobResult.data.session_file``
    is present in the result payload.

4.  Submit a second job with ``provider_session_id`` matching the persisted
    ``session_file`` path and assert ``switch_session`` is reflected in the
    progress events (no ``new_session`` call on the omp side).

5.  On failure::

        podman logs factory-omp
        podman exec factory-omp env | grep -E 'NATS|LITELLM|OMP'
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.omp.omp_pool import OmpPool, _PoolWorker
from factory.llm.drivers.omp_rpc import OmpRpcDriver
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_ok

# ---------------------------------------------------------------------------
# Module-level skip guard — all tests in this file require INTEGRATION=1
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.omp_contract,
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("INTEGRATION"),
        reason="set INTEGRATION=1 to run omp session-resume integration tests",
    ),
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mock_rpc_client(
    session_file: str = "/tmp/omp/.omp/sessions/sess-abc.jsonl",
) -> MagicMock:
    """Return a MagicMock shaped like omp_rpc.RpcClient."""
    client = MagicMock()
    client.start.return_value = None
    client.new_session.return_value = MagicMock()  # CancellationResult, NOT a path
    client.switch_session.return_value = None
    client.get_state.return_value = SimpleNamespace(session_file=session_file)
    client.stop.return_value = None
    return client


def _make_pool_with_fake_start(
    session_file: str = "/tmp/omp/.omp/sessions/sess-abc.jsonl",
) -> tuple[OmpPool, list[MagicMock]]:
    """OmpPool whose _start_worker returns fake _PoolWorker (client+bridge).
    Model B: patch internal starter; acquire(session_file) only (no pool_id).
    """
    pool = OmpPool(omp_bin=Path("/fake/omp"), provider="litellm", model="grok-4-fast")
    issued: list[MagicMock] = []

    async def _fake_start(self: OmpPool) -> _PoolWorker:  # type: ignore[override]
        client = _mock_rpc_client(session_file=session_file)
        issued.append(client)
        # dummy bridge (per-worker bridges not exercised in these direct-pool tests)
        bridge = MagicMock()
        return _PoolWorker(client=client, bridge=bridge)

    # Model B: _start_worker (not _start_client); returns _PoolWorker
    pool._start_worker = lambda: _fake_start(pool)  # type: ignore[method-assign]
    return pool, issued


def _make_store(*, cli_session: str | None = None) -> AsyncMock:
    """Return a fake _OmpSessionStore."""
    store = AsyncMock()
    store.get_cli_session = AsyncMock(return_value=cli_session)
    store._set_cli_session = AsyncMock()  # noqa: SLF001
    return store


def _make_nc_with_result(result: JobResult) -> tuple[AsyncMock, AsyncMock]:
    """Return (nc, sub) NATS mocks whose next_msg yields the given JobResult."""
    nc = AsyncMock()
    sub = AsyncMock()
    nc.subscribe.return_value = sub
    sub.next_msg.return_value = SimpleNamespace(data=result.model_dump_json().encode())
    return nc, sub


def _model_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump.return_value = {"backend": "omp-rpc", "model": "grok-4-fast"}
    return cfg


# ---------------------------------------------------------------------------
# Scenario (a): cold-start — session_file present in result AND persisted
# ---------------------------------------------------------------------------


class TestColdStartPersistsSessionFile:
    """Cold-start job: JobResult.data carries session_file and store is written."""

    @pytest.mark.asyncio
    async def test_cold_start_complete_returns_text(self) -> None:
        """Cold-start: complete() returns an ok LlmResult carrying the assistant text.

        The session_file rides JobResult.data and is persisted to the store — it is
        NOT surfaced on LlmResult (see test_session_file_persisted_in_store)."""
        _pool, _issued = _make_pool_with_fake_start()

        session_path = "/tmp/omp/.omp/sessions/sess-abc.jsonl"
        result = JobResult.model_validate(
            {
                **sample_job_result_ok,
                "data": {"result": "done", "session_file": session_path},
            }
        )
        nc, _sub = _make_nc_with_result(result)

        driver = OmpRpcDriver(nc, timeout_s=5.0)
        store = _make_store()
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-cold", "lyra-sess-cold")

        res = await driver.complete(
            pool_id="pool-cold",
            text="first task",
            model_cfg=_model_cfg(),
            system_prompt="sys",
        )

        assert res.ok is True
        assert res.result == "done"

    @pytest.mark.asyncio
    async def test_session_file_persisted_in_store(self) -> None:
        """After complete(), store._set_cli_session is called with the session path."""
        session_path = "/tmp/omp/.omp/sessions/sess-abc.jsonl"
        result = JobResult.model_validate(
            {
                **sample_job_result_ok,
                "data": {"result": "done", "session_file": session_path},
            }
        )
        nc, _sub = _make_nc_with_result(result)

        driver = OmpRpcDriver(nc, timeout_s=5.0)
        store = _make_store()
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-persist", "lyra-sess-persist")

        await driver.complete(
            pool_id="pool-persist",
            text="task",
            model_cfg=_model_cfg(),
            system_prompt="sys",
        )

        store._set_cli_session.assert_awaited_once_with(  # noqa: SLF001
            "lyra-sess-persist", session_path
        )


# ---------------------------------------------------------------------------
# Scenario (b): resume — switch_session called with the persisted session_file
# ---------------------------------------------------------------------------


class TestResumeCallsSwitchSession:
    """Resume path: queue_resume + complete → OmpPool.acquire uses persisted path."""

    @pytest.mark.asyncio
    async def test_switch_session_called_with_persisted_path(self) -> None:
        """Resuming a known session causes OmpPool to call switch_session on the
        RpcClient with the previously persisted session_file path.

        Flow:
          1. store pre-populated with cli_session token from a prior cold-start
          2. queue_resume() looks up the token → True
          3. OmpPool.acquire() called with that token → switch_session(token)
        """
        persisted_path = "/tmp/omp/.omp/sessions/persisted.jsonl"
        pool, issued = _make_pool_with_fake_start(session_file=persisted_path)

        # Simulate: store already has the persisted path from a prior job
        store = _make_store(cli_session=persisted_path)

        nc = AsyncMock()
        sub = AsyncMock()
        nc.subscribe.return_value = sub

        driver = OmpRpcDriver(nc, timeout_s=5.0)
        driver.set_turn_store(store)

        # queue_resume() must find the token in the store
        resumed = await driver.queue_resume(
            pool_id="pool-resume", session_id="lyra-sess-resume"
        )
        assert resumed is True, "queue_resume must return True when store has a token"

        # The pending token is stashed
        assert driver._pending_resume.get("pool-resume") == persisted_path  # noqa: SLF001

        # Now acquire slot with stashed session_file (Model B: no pool_id)
        pending_token = driver._pending_resume.pop("pool-resume")  # noqa: SLF001
        await pool.acquire(pending_token)

        # switch_session called with persisted path (inside acquire for resume)
        assert len(issued) == 1
        issued[0].switch_session.assert_called_once_with(persisted_path)
        issued[0].new_session.assert_not_called()
        issued[0].get_state.assert_not_called()

        await pool.aclose()

    @pytest.mark.asyncio
    async def test_queue_resume_returns_false_when_no_stored_session(self) -> None:
        """queue_resume returns False when the store has no prior session for the id."""
        nc = AsyncMock()
        nc.subscribe.return_value = AsyncMock()

        driver = OmpRpcDriver(nc, timeout_s=5.0)
        store = _make_store(cli_session=None)
        driver.set_turn_store(store)

        resumed = await driver.queue_resume(
            pool_id="pool-new", session_id="lyra-sess-new"
        )

        assert resumed is False
        assert "pool-new" not in driver._pending_resume  # noqa: SLF001


# ---------------------------------------------------------------------------
# Scenario (c): OmpPool cap=1 with two pool_ids — evicts without raising
# ---------------------------------------------------------------------------


class TestOmpPoolLruEviction:
    """OmpPool cap=1 (Model B): acquire beyond cap blocks until release.
    Flat free-set (no per-pool_id routing or auto-evict of checked-out).
    """

    @pytest.mark.asyncio
    async def test_cap1_two_pool_ids_evicts_without_raising(self) -> None:
        """OMP_POOL_CAP=1: second acquire blocks (no auto-evict).
        Release allows progress. (Old LRU-per-id removed in Model B.)
        """
        import asyncio
        import os
        from unittest.mock import patch

        pool, issued = _make_pool_with_fake_start()

        with patch.dict(os.environ, {"OMP_POOL_CAP": "1"}):
            w_a = await pool.acquire(None)  # Model B: acquire(sf); None=cold
            client_a = w_a.client
            assert len(issued) == 1

            # Second at cap=1 must block (no auto-evict on 'new pool_id')
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(pool.acquire(None), timeout=0.05)

            # Release first worker to free slot, then second acquire succeeds
            pool.release(w_a)
            w_b = await pool.acquire(None)
            client_b = w_b.client

        assert len(issued) == 2

        # Model B: flat free-set, no _entries. Concurrency via Sem+release.
        # (No _entries accesses per new API.)

        # No stop on 'second' acquire; stops on aclose/explicit.
        client_a.stop.assert_not_called()
        client_b.stop.assert_not_called()

        await pool.aclose()

    @pytest.mark.asyncio
    async def test_cap1_stop_error_does_not_propagate(self) -> None:
        """Stop errs (now only aclose) swallowed; acquire/release guarantee
        forward progress under cap (semaphore)."""
        import os
        from unittest.mock import patch

        pool, issued = _make_pool_with_fake_start()

        with patch.dict(os.environ, {"OMP_POOL_CAP": "1"}):
            w1 = await pool.acquire(None)
            assert len(issued) == 1
            c1 = w1.client

            # Make stop() raise on first (will be hit on aclose, not acquire)
            c1.stop.side_effect = RuntimeError("omp died")

            # Release so second acquire can proceed under cap=1 (no auto-evict)
            pool.release(w1)

            # Must succeed — forward progress
            w2 = await pool.acquire(None)
            c2 = w2.client

        assert len(issued) == 2
        assert c2 is issued[1]

        # aclose swallows stop errs (see impl)
        c2.stop.side_effect = None
        await pool.aclose()
