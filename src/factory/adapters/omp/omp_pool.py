"""OmpPool — flat free-set of warm (RpcClient, RpcBridge) workers.

Any free worker serves any conversation after switch_session — the pool no
longer routes by pool_id. M interchangeable workers share a single asyncio.Semaphore
so concurrency is RAM-bounded (each worker ≈ one omp subprocess).

Session lifecycle:
  - acquire(session_file)  → switch_session(session_file) on the checked-out worker.
  - acquire(None)          → new_session() + get_state() to mint the .jsonl path.
  - release(worker)        → return to the free set; semaphore slot released.

Usage::

    pool = OmpPool()
    await pool.register(nc)           # called once by OmpWorker.run before any acquire
    worker = await pool.acquire(session_file)
    # ... use worker.client / worker.bridge ...
    pool.release(worker)
    await pool.aclose()
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# omp binary path — image-build constant (NEVER from env). The sha256 pin + its
# verification live in _rpc_bridge (_verify_digest) — reused, not duplicated.
_OMP_BIN = Path("/opt/omp/omp")
_DEFAULT_PROVIDER = "litellm"

# max concurrent omp workers — size to available RAM (each worker ≈ one omp subprocess).
_ENV_CAP_KEY = "OMP_POOL_CAP"
_DEFAULT_CAP = 4


def _read_cap() -> int:
    """Read OMP_POOL_CAP from env; fall back to _DEFAULT_CAP.

    Meaning: maximum number of concurrent omp workers. Size to available RAM —
    each worker is one omp subprocess with its own memory footprint.
    """
    raw = os.environ.get(_ENV_CAP_KEY, "")
    if raw.strip().isdigit():
        val = int(raw.strip())
        return val if val > 0 else _DEFAULT_CAP
    return _DEFAULT_CAP


@dataclass
class _PoolWorker:
    client: Any  # omp_rpc.RpcClient (already started)
    bridge: Any  # RpcBridge(_client=client), attached to nc
    session_file: str | None = None


class OmpPool:
    """Flat free-set of warm (RpcClient, RpcBridge) workers.

    Workers are interchangeable — routing by pool_id is gone. The semaphore caps
    concurrency at OMP_POOL_CAP (env, default 4); workers are lazily started on
    first demand and reused across conversations via switch_session.

    Thread-safety: all methods are async and must be called from the event loop.
    Blocking omp_rpc calls are dispatched via asyncio.to_thread.

    Usage::

        pool = OmpPool()
        await pool.register(nc)
        worker = await pool.acquire(session_file)
        # ... use worker.client / worker.bridge ...
        pool.release(worker)
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
        self._free: list[_PoolWorker] = []
        self._all: list[_PoolWorker] = []
        self._checked_out: set[int] = set()  # ids (workers not hashable)
        self._sem = asyncio.Semaphore(
            _read_cap()
        )  # safe in __init__ (Lock did the same)
        self._nc: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register(self, nc: Any) -> None:
        """Store nc + loop so _start_worker can attach each worker's bridge.

        MUST be called (by OmpWorker.run) before any acquire().
        """
        self._nc = nc
        self._loop = asyncio.get_running_loop()

    async def acquire(self, session_file: str | None) -> _PoolWorker:
        """Check out a free worker (or lazily start one, up to M).

        Resume the durable session via switch_session if a token was given,
        else new_session + get_state to mint the .jsonl path.
        """
        await self._sem.acquire()
        try:
            worker = self._free.pop() if self._free else await self._start_worker()
        except BaseException:
            self._sem.release()  # never leak a slot if cold-start failed
            raise
        self._checked_out.add(id(worker))
        try:
            if session_file is not None:
                await asyncio.to_thread(worker.client.switch_session, session_file)
                worker.session_file = session_file
                log.debug("[omp_pool] acquire with session %s", session_file)
            else:
                # new_session() returns a CancellationResult — NOT the path.
                # Call get_state() to obtain the minted .jsonl session_file path.
                await asyncio.to_thread(worker.client.new_session)
                state = await asyncio.to_thread(worker.client.get_state)
                worker.session_file = getattr(state, "session_file", None)
                log.debug("[omp_pool] acquire new session, minted %s", worker.session_file)  # noqa: E501
            return worker
        except BaseException:
            self._checked_out.discard(id(worker))
            self._sem.release()  # caller finally skips assign on exc
            # worker not returned (orphaned in _all; aclose will stop)
            raise

    def release(self, worker: _PoolWorker) -> None:
        """Return a worker to the free set and release its semaphore slot."""
        was_checked = id(worker) in self._checked_out
        self._checked_out.discard(id(worker))
        if was_checked:
            if worker not in self._free:
                self._free.append(worker)
            self._sem.release()
        else:
            log.warning("[omp_pool] release of untracked worker (dup or error path?)")

    async def aclose(self) -> None:
        """Stop all running workers. Safe to call multiple times."""
        for w in self._all:
            try:
                await asyncio.to_thread(w.client.stop)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                log.warning("[omp_pool] client.stop raised", exc_info=True)
        self._free.clear()
        self._all.clear()
        self._checked_out.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_worker(self) -> _PoolWorker:
        """Construct, start, and attach a fresh digest-pinned worker."""
        # Deferred import — omp_rpc is a container image dep (absent from pyproject).
        import omp_rpc  # type: ignore[import-not-found]

        from factory.adapters.omp._rpc_bridge import RpcBridge, _verify_digest

        # gate before client (non-blocking via to_thread)
        await asyncio.to_thread(_verify_digest, self._omp_bin)
        client: Any = None
        try:
            client = omp_rpc.RpcClient(
                executable=str(self._omp_bin),
                provider=self._provider,
                model=self._model,
                no_session=False,
            )
            await asyncio.to_thread(client.start)
            # RpcBridge(_client=): skips digest (done) + no own ctor.
            bridge = RpcBridge(
                omp_bin=self._omp_bin,
                provider=self._provider,
                model=self._model,
                _client=client,
            )
            # attach() wires callbacks + stores nc/loop; no start, no new_session.
            # (we started it above; acquire() owns session selection).
            await bridge.attach(self._nc, self._loop)
            worker = _PoolWorker(client=client, bridge=bridge)
            self._all.append(worker)
            return worker
        except BaseException:
            if client is not None:
                try:
                    await asyncio.to_thread(client.stop)
                except Exception:  # noqa: BLE001
                    log.warning("omp_pool: stop leaked client failed", exc_info=True)  # noqa: E501
            raise
