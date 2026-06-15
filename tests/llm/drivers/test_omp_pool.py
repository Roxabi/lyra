"""Tests for OmpPool — worker-side LRU pool of warm omp_rpc.RpcClients.

Four scenarios:
  1. warm-hit reuse — second acquire returns the same client, no re-start/new_session
  2. cold-start-with-token — switch_session() called with the provided session_file
  3. cold-start-no-token — new_session() + get_state() called; minted path stored
  4. LRU evict at cap — oldest entry is stopped when cap is reached
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from factory.adapters.omp.omp_pool import _DEFAULT_CAP, OmpPool, _read_cap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_rpc_client(session_file: str = "/tmp/session.jsonl") -> MagicMock:
    """Return a MagicMock whose blocking RpcClient methods record calls."""
    client = MagicMock()
    client.start.return_value = None
    client.new_session.return_value = MagicMock()  # CancellationResult, NOT a path
    client.switch_session.return_value = None
    # get_state returns an object with a session_file attribute
    client.get_state.return_value = SimpleNamespace(session_file=session_file)
    client.stop.return_value = None
    return client


def _make_pool_with_fake_start(
    clients: list[MagicMock] | None = None,
) -> tuple[OmpPool, list[MagicMock]]:
    """Return an OmpPool whose _start_client is patched to hand out fake clients."""
    pool = OmpPool(omp_bin=Path("/fake/omp"), provider="litellm", model="grok-4-fast")
    issued: list[MagicMock] = clients if clients is not None else []

    async def _fake_start(self: OmpPool) -> MagicMock:  # type: ignore[override]
        client = _mock_rpc_client()
        client.start()  # mirror real _start_client (constructs + starts the client)
        issued.append(client)
        return client

    pool._start_client = lambda: _fake_start(pool)  # type: ignore[method-assign]
    return pool, issued


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpPool:
    # -- 1. warm-hit reuse ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_warm_hit_reuses_same_client(self) -> None:
        """Second acquire for the same pool_id returns the exact same client
        without calling start(), new_session(), or switch_session() again."""
        pool, issued = _make_pool_with_fake_start()

        first = await pool.acquire("user:1", session_file=None)
        second = await pool.acquire("user:1", session_file=None)

        assert first is second, "warm hit must return the same client object"
        assert len(issued) == 1, "only one client should have been constructed"
        # start() called exactly once (during cold start)
        issued[0].start.assert_called_once()
        # new_session called once (cold start), NOT on the warm hit
        assert issued[0].new_session.call_count == 1

        await pool.aclose()

    # -- 2. cold-start with session_file (switch_session) --------------------

    @pytest.mark.asyncio
    async def test_cold_start_with_token_calls_switch_session(self) -> None:
        """Cold start with a non-None session_file calls switch_session with
        that path and does NOT call new_session or get_state."""
        pool, issued = _make_pool_with_fake_start()
        token = "/home/.omp/sessions/abc123.jsonl"

        client = await pool.acquire("user:2", session_file=token)

        assert len(issued) == 1
        issued[0].switch_session.assert_called_once_with(token)
        issued[0].new_session.assert_not_called()
        issued[0].get_state.assert_not_called()

        # The returned client is the one we handed out
        assert client is issued[0]

        await pool.aclose()

    # -- 3. cold-start without token (new_session + get_state) ---------------

    @pytest.mark.asyncio
    async def test_cold_start_no_token_calls_new_session_and_get_state(
        self,
    ) -> None:
        """Cold start with session_file=None calls new_session() then get_state()
        to obtain the minted .jsonl path.  switch_session must NOT be called."""
        pool, issued = _make_pool_with_fake_start()

        client = await pool.acquire("user:3", session_file=None)

        assert len(issued) == 1
        issued[0].new_session.assert_called_once()
        issued[0].get_state.assert_called_once()
        issued[0].switch_session.assert_not_called()

        # The minted session path is stored on the pool entry
        entry = pool._entries["user:3"]
        assert entry.session_file == "/tmp/session.jsonl"

        assert client is issued[0]

        await pool.aclose()

    # -- 4. LRU evict at cap -------------------------------------------------

    @pytest.mark.asyncio
    async def test_lru_evict_at_cap_stops_oldest_entry(self) -> None:
        """When the pool is at cap (OMP_POOL_CAP=2 for this test), acquiring a
        new pool_id evicts the least-recently-used entry and calls stop() on it."""
        pool, issued = _make_pool_with_fake_start()

        # Patch cap to 2 so the test is deterministic and fast
        with patch.dict(os.environ, {"OMP_POOL_CAP": "2"}):
            # Fill pool to cap
            client_a = await pool.acquire("user:A", session_file=None)
            client_b = await pool.acquire("user:B", session_file=None)

            assert len(issued) == 2
            assert len(pool._entries) == 2

            # Touch B again to make A the LRU
            await pool.acquire("user:B", session_file=None)

            # Acquire C — should evict A (LRU)
            client_c = await pool.acquire("user:C", session_file=None)

        assert len(issued) == 3
        # Pool should have B and C; A is evicted
        assert "user:A" not in pool._entries
        assert "user:B" in pool._entries
        assert "user:C" in pool._entries
        assert len(pool._entries) == 2

        # stop() must have been called on A's client
        client_a.stop.assert_called_once()
        # B and C are still alive (stop not called yet)
        client_b.stop.assert_not_called()
        client_c.stop.assert_not_called()

        await pool.aclose()

    # -- 5. aclose stops all clients -----------------------------------------

    @pytest.mark.asyncio
    async def test_aclose_stops_all_clients(self) -> None:
        """aclose() calls stop() on every live client and clears _entries."""
        pool, issued = _make_pool_with_fake_start()

        await pool.acquire("user:X", session_file=None)
        await pool.acquire("user:Y", session_file="/tmp/y.jsonl")
        assert len(issued) == 2

        await pool.aclose()

        for client in issued:
            client.stop.assert_called_once()
        assert len(pool._entries) == 0

    # -- 6. _read_cap falls back to default ----------------------------------

    def test_read_cap_defaults_to_four(self) -> None:
        """_read_cap() returns _DEFAULT_CAP when OMP_POOL_CAP is unset."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OMP_POOL_CAP", None)
            assert _read_cap() == _DEFAULT_CAP
            assert _DEFAULT_CAP == 4

    def test_read_cap_honours_env_var(self) -> None:
        """_read_cap() returns the integer value of OMP_POOL_CAP when set."""
        with patch.dict(os.environ, {"OMP_POOL_CAP": "7"}):
            assert _read_cap() == 7
