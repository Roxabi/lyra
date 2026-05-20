"""WorkerPoolClient — hub-side worker pool with CB + registry + heartbeat.

Composes a transport (NATS today, HTTP tomorrow). Owns CircuitBreaker
+ WorkerRegistry + heartbeat subscription. Domain clients (LLM, TTS, STT,
Image) call request_with_routing() / stream_request(); pool stays
domain-agnostic. Spec § Slice S3. Consensus § B2.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Protocol

from lyra.nats.worker_registry import WorkerRegistry
from lyra.transport._result import Err, InboxStream, Ok, Result, SanitizedError
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


class _TransportLike(Protocol):
    """Structural type for the transport layer dependency.

    NatsTransport (transport/nats_request_response.py) satisfies this.
    P4 HttpTransport (#1281) will satisfy `call` only; `open_inbox`
    raises NotImplementedError there.
    """

    async def call(
        self, subject: str, payload: bytes, *, timeout: float | None = None
    ) -> Result[bytes, SanitizedError]: ...

    def open_inbox(self) -> AbstractAsyncContextManager[InboxStream]: ...


class WorkerPoolClient:
    def __init__(
        self,
        transport: _TransportLike,
        *,
        hb_subject: str,
        validate_worker_id: Callable[[str], None],
        hb_ttl: float = 15.0,
        name: str = "pool",
    ) -> None:
        self._transport = transport
        self._registry: WorkerRegistry = WorkerRegistry()
        self._cb: NatsCircuitBreaker = NatsCircuitBreaker()
        self._hb_subject = hb_subject
        self._validate_worker_id = validate_worker_id
        self._hb_ttl = hb_ttl
        self._name = name
        self._sub: Any = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, nc: "NATS") -> None:
        self._sub = await nc.subscribe(self._hb_subject, cb=self._on_heartbeat)
        self._task = asyncio.create_task(
            self._heartbeat_loop(), name=f"{self._name}.hb"
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._sub:
            await self._sub.unsubscribe()

    async def _on_heartbeat(self, msg: Any) -> None:
        """Dual-validation heartbeat handler — replayed from NatsWorkerClientBase.

        1. parse JSON payload -> extract worker_id
        2. VALIDATE_WORKER_ID(worker_id) — primary guard (subclass-injected)
        3. WorkerRegistry secondary guard via record_heartbeat (validates nats_token)
        Both checks preserved for defense-in-depth per ADR-045.
        """
        try:
            payload = json.loads(msg.data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("%s.hb_decode_error err=%s", self._name, type(exc).__name__)
            return
        worker_id = payload.get("worker_id")
        if not isinstance(worker_id, str) or not worker_id:
            return
        try:
            self._validate_worker_id(worker_id)
        except ValueError as exc:
            log.warning(
                "%s.hb_validate_fail worker_id=%s err=%s", self._name, worker_id, exc
            )
            return
        self._registry.record_heartbeat(payload)

    async def _heartbeat_loop(self) -> None:
        """Periodic TTL sweep — alive_workers() prunes stale entries on each call."""
        try:
            while True:
                await asyncio.sleep(self._hb_ttl)
                # WorkerRegistry._prune() is invoked internally by alive_workers();
                # calling any_alive() here triggers the prune without extra overhead.
                self._registry.any_alive()
        except asyncio.CancelledError:
            return

    def is_pool_alive(self) -> bool:
        return self._registry.any_alive()

    async def request_with_routing(
        self,
        subject_fn: Callable[[str], str],
        payload: bytes,
        *,
        timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> Result[bytes, SanitizedError]:
        """Iterate scored workers; subject_fn(worker_id) -> subject.

        Emits mandatory structured log (pool, worker_id, subject, attempt, result)
        before each transport.call(). Consensus § B2.
        """
        if self._cb.is_open():
            return Err(
                SanitizedError(
                    code="pool.circuit_open", message="CircuitOpen", retryable=True
                )
            )
        attempt = 0
        for worker in self._registry.ordered_by_score():
            if max_attempts is not None and attempt >= max_attempts:
                break
            subject = subject_fn(worker.worker_id)
            log.info(
                "pool.routing",
                extra={
                    "pool": self._name,
                    "worker_id": worker.worker_id,
                    "subject": subject,
                    "attempt": attempt,
                    "result": "pending",
                },
            )
            result = await self._transport.call(subject, payload, timeout=timeout)
            attempt += 1
            if isinstance(result, Ok):
                self._cb.record_success()
                return result
            if result.error.code in {"transport.timeout", "transport.no_responders"}:
                self._registry.mark_stale(worker.worker_id)
            self._cb.record_failure()
        log.warning("pool.no_live_workers pool=%s", self._name)
        return Err(
            SanitizedError(
                code="pool.no_live_workers", message="NoLiveWorkers", retryable=True
            )
        )

    async def stream_request(
        self, payload: bytes, *, timeout: float | None = None
    ) -> AsyncIterator[Result[bytes, SanitizedError]]:
        """Compose transport.open_inbox. Yields Result[bytes, SanitizedError] chunks."""
        if self._cb.is_open():
            yield Err(
                SanitizedError(
                    code="pool.circuit_open", message="CircuitOpen", retryable=True
                )
            )
            return
        async with self._transport.open_inbox() as stream:
            # Domain layer publishes the request with the inbox subject as reply field;
            # workers stream responses into the inbox.
            async for msg in stream.messages:
                yield msg
