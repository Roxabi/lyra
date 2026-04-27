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
import re
import signal
import socket
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import cast

from nats.aio.client import Client as NATS

# NatsAdapterBase uses _CONTRACT_VERSION directly from its canonical home.
# The public name CONTRACT_VERSION is served via __getattr__ below so that
# accessing it emits a DeprecationWarning per ADR-059 (V4).
from roxabi_contracts.envelope import CONTRACT_VERSION as _CONTRACT_VERSION
from roxabi_nats._serialize import _EMPTY_RESOLVER, _TypeHintResolver
from roxabi_nats._validate import validate_nats_token
from roxabi_nats._version_check import (
    check_contract_version,
    check_schema_version,
)
from roxabi_nats.connect import nats_connect
from roxabi_nats.readiness import wait_for_hub

__all__ = ["CONTRACT_VERSION", "NatsAdapterBase"]  # noqa: F822  # pyright: ignore[reportUnsupportedDunderAll]


def __getattr__(name: str) -> object:
    """Lazy shim — emits DeprecationWarning only when CONTRACT_VERSION is accessed
    via ``roxabi_nats.adapter_base.CONTRACT_VERSION``.  Plain ``import`` of this
    module no longer fires any warning.

    Compat shim per ADR-059 (V4). Remove at roxabi-nats v0.3.0.
    """
    if name == "CONTRACT_VERSION":
        import warnings

        warnings.warn(
            "roxabi_nats.adapter_base.CONTRACT_VERSION "
            "(and roxabi_nats.CONTRACT_VERSION) is deprecated; "
            "import from roxabi_contracts.envelope instead. "
            "The re-export is removed at roxabi-nats v0.3.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CONTRACT_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


log = logging.getLogger(__name__)


class NatsAdapterBase(ABC):
    def __init__(  # noqa: PLR0913
        self,
        subject,
        queue_group,
        envelope_name,
        schema_version,
        timeout=30.0,
        drain_timeout=30.0,
        *,
        heartbeat_subject: str | None = None,
        heartbeat_interval: float = 5.0,
        type_registry: Sequence[tuple[str, str]] | None = None,
        inbox_prefix: str | None = None,
        identity_name: str | None = None,
    ):
        if inbox_prefix is not None and identity_name is not None:
            raise ValueError(
                "NatsAdapterBase: inbox_prefix and identity_name are mutually exclusive"
            )
        validate_nats_token(subject, kind="subject")
        validate_nats_token(queue_group, kind="queue_group")
        self.subject = subject
        self.queue_group = queue_group
        self.envelope_name = envelope_name
        self.schema_version = schema_version
        self.timeout = timeout
        self.drain_timeout = drain_timeout
        # Per-identity inbox prefix (ADR-051). Use identity_name (preferred, canonical)
        # or inbox_prefix (legacy, for callers that pre-date identity_name).
        # Forwarded to nats_connect() at run() time. Mutually exclusive.
        self._inbox_prefix = inbox_prefix
        self._identity_name = identity_name
        self._nc: NATS | None = None
        self._drop_count: dict[str, int] = {}
        self._started_at: float | None = None
        self._heartbeat_subject = heartbeat_subject
        self._heartbeat_interval = heartbeat_interval
        # Hostnames may contain dots (FQDN) which are NATS subject delimiters,
        # and colons/spaces which are not allowed tokens. Sanitize to the
        # NATS-safe alphabet so publish and subscribe sides agree on the same
        # subject token regardless of host.
        raw_id = f"{queue_group}-{socket.gethostname()}-{os.getpid()}"
        self._worker_id = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id)
        self._heartbeat_task: asyncio.Task | None = None
        self._resolver: _TypeHintResolver = (
            _TypeHintResolver(type_registry)
            if type_registry is not None
            else _EMPTY_RESOLVER
        )

    async def run(self, nats_url: str, stop: asyncio.Event | None = None) -> None:
        nc = await nats_connect(
            nats_url,
            identity_name=self._identity_name,
            inbox_prefix=self._inbox_prefix,
        )
        self._nc = nc
        await self._wait_ready()
        await nc.subscribe(self.subject, queue=self.queue_group, cb=self._dispatch)
        for extra in self._extra_subjects():
            await nc.subscribe(extra, cb=self._dispatch)
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

    async def run_embedded(self, nc: NATS, stop: asyncio.Event | None = None) -> None:
        """Run using an already-connected NATS client (for unified/embedded mode).

        Unlike ``run()``, this method does not create a new NATS connection and
        does not call ``_shutdown()`` (which would drain/close the shared connection).
        The caller is responsible for managing the NATS connection lifecycle.
        """
        self._nc = nc
        self._started_at = time.monotonic()
        cmd_sub = await nc.subscribe(
            self.subject, queue=self.queue_group, cb=self._dispatch
        )
        subs = [cmd_sub]
        for extra in self._extra_subjects():
            subs.append(await nc.subscribe(extra, cb=self._dispatch))
        if self._heartbeat_subject:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if stop is None:
            stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
            for sub in subs:
                with contextlib.suppress(Exception):
                    await sub.unsubscribe()

    @abstractmethod
    async def handle(self, msg, payload: dict) -> None: ...

    def _extra_subjects(self) -> list[str]:
        """Return additional subjects to subscribe to (no queue group).

        Default is empty. Subclasses override to add per-instance routing —
        e.g. a voice adapter returns ``[f"{self.subject}.{self._worker_id}"]``
        so the hub can target it directly via its worker id.
        """
        return []

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
        # Sequential short-circuit: each check logs and counts its own drop, so
        # we stop at the first failure to avoid duplicate lines for a doubly-
        # malformed payload. Add any future checks here in priority order.
        if not check_schema_version(
            payload,
            envelope_name=self.envelope_name,
            expected=self.schema_version,
            subject=self.subject,
            counter=self._drop_count,
        ):
            return False
        return check_contract_version(
            payload,
            envelope_name=self.envelope_name,
            expected=_CONTRACT_VERSION,
            subject=self.subject,
            counter=self._drop_count,
        )

    def heartbeat_payload(self) -> dict:
        """Base heartbeat payload. Subclasses override to add service fields."""
        uptime = time.monotonic() - self._started_at if self._started_at else 0.0
        return {
            "contract_version": _CONTRACT_VERSION,
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
        # caller guarantees _heartbeat_subject is set (see start() guard)
        subject = cast(str, self._heartbeat_subject)
        while self._nc and not self._nc.is_closed:
            if not self._nc.is_connected:
                await asyncio.sleep(1.0)
                continue
            try:
                payload = self.heartbeat_payload()
                await self._nc.publish(
                    subject,
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
