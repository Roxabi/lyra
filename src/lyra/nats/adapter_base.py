"""NatsAdapterBase — ABC lifecycle host for NATS request-reply adapters.

Subclass this and implement ``handle(msg)`` to build a NATS queue-subscriber
adapter with built-in envelope validation, hub readiness waiting, graceful
drain/close shutdown, and a ``health()`` introspection method.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import time
from abc import ABC, abstractmethod

from nats.aio.client import Client as NATS

from lyra.nats._validate import validate_nats_token
from lyra.nats._version_check import check_schema_version
from lyra.nats.connect import nats_connect
from lyra.nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


class NatsAdapterBase(ABC):
    def __init__(  # noqa: PLR0913
        self, subject, queue_group, envelope_name, schema_version,
        timeout=30.0, drain_timeout=30.0,
        *, heartbeat_subject: str | None = None, heartbeat_interval: float = 5.0,
    ):
        validate_nats_token(subject, kind="subject")
        validate_nats_token(queue_group, kind="queue_group")
        self.subject = subject
        self.queue_group = queue_group
        self.envelope_name = envelope_name
        self.schema_version = schema_version
        self.timeout = timeout
        self.drain_timeout = drain_timeout
        self._nc: NATS | None = None
        self._drop_count: dict[str, int] = {}
        self._started_at: float | None = None
        self._heartbeat_subject = heartbeat_subject
        self._heartbeat_interval = heartbeat_interval
        self._worker_id = f"{queue_group}-{socket.gethostname()}-{os.getpid()}"
        self._heartbeat_task: asyncio.Task | None = None

    async def run(self, nats_url: str, stop: asyncio.Event | None = None) -> None:
        nc = await nats_connect(nats_url)
        self._nc = nc
        await self._wait_ready()
        await nc.subscribe(self.subject, queue=self.queue_group, cb=self._dispatch)
        if self._heartbeat_subject:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if stop is None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
        self._started_at = time.monotonic()
        await stop.wait()
        await self._shutdown()

    @abstractmethod
    async def handle(self, msg, payload: dict) -> None: ...

    async def reply(self, msg, data: bytes) -> None:
        """Publish a response to msg.reply if a reply subject exists."""
        if msg.reply and self._nc:
            await self._nc.publish(msg.reply, data)

    async def _dispatch(self, msg) -> None:
        try:
            payload = json.loads(msg.data)
        except Exception:
            log.error("adapter_base: malformed JSON on %s", self.subject)
            return
        if self._validate_envelope(payload):
            await self.handle(msg, payload)

    def _validate_envelope(self, payload: dict) -> bool:
        return check_schema_version(
            payload,
            envelope_name=self.envelope_name,
            expected=self.schema_version,
            subject=self.subject,
            counter=self._drop_count,
        )

    def heartbeat_payload(self) -> dict:
        """Base heartbeat payload. Subclasses override to add service fields."""
        uptime = time.monotonic() - self._started_at if self._started_at else 0.0
        return {
            "worker_id": self._worker_id,
            "service": self.queue_group,
            "host": socket.gethostname(),
            "subject": self.subject,
            "queue_group": self.queue_group,
            "connected": self._nc.is_connected if self._nc else False,
            "uptime_s": uptime,
            "ts": time.time(),
        }

    async def _heartbeat_loop(self) -> None:
        while self._nc and self._nc.is_connected:
            try:
                payload = self.heartbeat_payload()
                await self._nc.publish(
                    self._heartbeat_subject,  # type: ignore[arg-type]
                    json.dumps(payload).encode(),
                )
            except Exception:
                log.warning("adapter_base: heartbeat publish failed", exc_info=True)
            await asyncio.sleep(self._heartbeat_interval)

    async def _shutdown(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._nc:
            await asyncio.wait_for(self._nc.drain(), timeout=self.drain_timeout)
            await self._nc.close()

    async def _wait_ready(self) -> None:
        if self._nc is None:
            raise RuntimeError(  # noqa: TRY003
                "_wait_ready called before NATS connection was established"
            )
        ok = await wait_for_hub(self._nc, timeout=self.timeout)
        if not ok:
            log.warning("adapter_base: hub readiness timed out — starting anyway")

    def health(self) -> dict:
        uptime = time.monotonic() - self._started_at if self._started_at else 0.0
        return {
            "status": "ok",
            "subject": self.subject,
            "queue_group": self.queue_group,
            "schema_version": self.schema_version,
            "connected": self._nc.is_connected if self._nc else False,
            "uptime_s": round(uptime, 3),
        }
