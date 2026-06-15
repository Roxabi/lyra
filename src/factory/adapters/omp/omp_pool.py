"""OmpPool — worker-side pool of warm omp RpcClients keyed by pool_id.

Manages a bounded set of warm omp_rpc.RpcClient instances so that repeated
calls from the same conversation scope reuse the same running process.

LRU eviction caps the pool at OMP_POOL_CAP (env, default 4); the least-recently
used entry is stopped and removed to make room. The cap is never a hard error —
forward progress is always guaranteed.

Session lifecycle:
  - Cold-start with a session_file  → client.switch_session(session_file)
  - Cold-start without a session_file → client.new_session(), then client.get_state()
    to obtain the minted .jsonl path (new_session returns a CancellationResult).
  - Warm-hit → reuse the already-running client; session_file is ignored.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Image-build constants — NEVER from env (mirrored from _rpc_bridge.py).
_PINNED_SHA256 = "b877091c91ebdc8c8d907c4b62681895cd3ae049815858aea7b69ac1d53b7c7b"
_OMP_BIN = Path("/opt/omp/omp")
_DEFAULT_PROVIDER = "litellm"

# Cap is a runtime config knob — read from env, never a bare literal.
_ENV_CAP_KEY = "OMP_POOL_CAP"
_DEFAULT_CAP = 4


def _read_cap() -> int:
    """Read OMP_POOL_CAP from env; fall back to _DEFAULT_CAP."""
    raw = os.environ.get(_ENV_CAP_KEY, "")
    if raw.strip().isdigit():
        val = int(raw.strip())
        return val if val > 0 else _DEFAULT_CAP
    return _DEFAULT_CAP


@dataclass
class _PoolEntry:
    """One live omp_rpc.RpcClient with its access-order index."""

    client: Any  # omp_rpc.RpcClient
    session_file: str | None  # path to the .jsonl session, None until first get_state
    _lru_seq: int = field(default=0, compare=False)


class OmpPool:
    """Bounded LRU pool of warm omp_rpc.RpcClient instances.

    Thread-safety: all methods are async and must be called from the event loop.
    Blocking omp_rpc calls are dispatched via asyncio.to_thread.

    Usage::

        pool = OmpPool()
        client = await pool.acquire(pool_id, session_file=None)
        # ... use client ...
        pool.release(pool_id)  # no-op today; reserved for future lock-based API
        await pool.aclose()
    """

    def __init__(
        self,
        *,
        omp_bin: Path = _OMP_BIN,
        provider: str | None = _DEFAULT_PROVIDER,
        model: str | None = None,
    ) -> None:
        self._omp_bin = omp_bin
        self._provider = provider
        self._model = model
        self._entries: dict[str, _PoolEntry] = {}
        self._lru_counter: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self, pool_id: str, session_file: str | None) -> Any:
        """Return a warm omp_rpc.RpcClient for *pool_id*.

        Warm hit: existing client is returned immediately; session_file is ignored.
        Cold start: a new RpcClient is constructed, started, and the session is
        established (switch_session if session_file given, else new_session +
        get_state to mint the .jsonl path).

        LRU eviction fires when the pool is at cap before the cold start.
        """
        entry = self._entries.get(pool_id)
        if entry is not None:
            # Warm hit — bump LRU and return.
            self._lru_counter += 1
            entry._lru_seq = self._lru_counter
            log.debug("[omp_pool:%s] warm hit", pool_id)
            return entry.client

        # Cold start — evict the LRU entry if at cap.
        await self._maybe_evict()

        client = await self._start_client()
        minted_session: str | None = None

        if session_file is not None:
            await asyncio.to_thread(client.switch_session, session_file)
            minted_session = session_file
            log.debug("[omp_pool:%s] cold start with session %s", pool_id, session_file)
        else:
            # new_session() returns a CancellationResult — NOT the path.
            # Call get_state() to obtain the minted .jsonl session_file path.
            await asyncio.to_thread(client.new_session)
            state = await asyncio.to_thread(client.get_state)
            minted_session = getattr(state, "session_file", None)
            log.debug(
                "[omp_pool:%s] cold start, minted session %s", pool_id, minted_session
            )

        self._lru_counter += 1
        self._entries[pool_id] = _PoolEntry(
            client=client,
            session_file=minted_session,
            _lru_seq=self._lru_counter,
        )
        return client

    def release(self, pool_id: str) -> None:  # noqa: ARG002
        """Mark pool_id as released.

        No-op in the current single-consumer-per-slot design; reserved for a
        future lock-based acquire/release protocol.
        """

    async def aclose(self) -> None:
        """Stop all running clients. Safe to call multiple times."""
        for pool_id, entry in list(self._entries.items()):
            await self._stop_client(pool_id, entry.client)
        self._entries.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_client(self) -> Any:
        """Construct and start a fresh omp_rpc.RpcClient."""
        # Import deferred: omp_rpc is a container image dep, absent from pyproject.toml.
        import omp_rpc  # type: ignore[import-not-found]

        client: Any = omp_rpc.RpcClient(
            executable=str(self._omp_bin),
            provider=self._provider,
            model=self._model,
            no_session=True,
        )
        await asyncio.to_thread(client.start)
        return client

    async def _stop_client(self, pool_id: str, client: Any) -> None:
        """Stop a client, logging but swallowing errors (best-effort cleanup)."""
        try:
            await asyncio.to_thread(client.stop)
        except Exception:  # noqa: BLE001  — DEBT:boundary-broad-catch# pool cleanup
            log.warning("[omp_pool:%s] client.stop raised", pool_id, exc_info=True)

    async def _maybe_evict(self) -> None:
        """Evict the LRU entry if the pool is at or above cap."""
        cap = _read_cap()
        if len(self._entries) < cap:
            return
        # Find the entry with the smallest LRU sequence (least recently used).
        lru_pool_id = min(self._entries, key=lambda k: self._entries[k]._lru_seq)
        lru_entry = self._entries.pop(lru_pool_id)
        log.info("[omp_pool] LRU evict pool_id=%s (cap=%d)", lru_pool_id, cap)
        await self._stop_client(lru_pool_id, lru_entry.client)
