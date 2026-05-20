"""NatsTransport — request/response + inbox primitives over NATS.

Owns NATS-specific resources (Client ref, inbox lifecycle, sanitization).
Spec: artifacts/specs/1278-nats-transport-workerpool-spec.mdx (S2).
Consensus: artifacts/analyses/1278-nats-transport-workerpool-consensus.mdx (B1 hybrid).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

import nats.errors
from nats.errors import MaxPayloadError, NoRespondersError

from lyra.transport._result import Err, InboxStream, Ok, Result, SanitizedError

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


class NatsTransport:
    def __init__(self, nc: "NATS", *, default_timeout: float = 120.0) -> None:
        self._nc = nc
        self._default_timeout = default_timeout

    async def publish(
        self, subject: str, payload: bytes, *, reply_subject: str
    ) -> None:
        """Fire-and-forget publish with explicit reply subject.

        Used by streaming paths: callers open an inbox via `open_inbox()` and
        pass its subject here so workers stream chunks back to the inbox.
        """
        await self._nc.publish(subject, payload, reply=reply_subject)

    async def call(
        self, subject: str, payload: bytes, *, timeout: float | None = None
    ) -> Result[bytes, SanitizedError]:
        t = timeout if timeout is not None else self._default_timeout
        try:
            reply = await self._nc.request(subject, payload, timeout=t)
            return Ok(reply.data)
        except TimeoutError as exc:
            return Err(self._sanitize(exc, context="call.timeout"))
        except NoRespondersError as exc:
            return Err(self._sanitize(exc, context="call.no_responders"))
        except nats.errors.Error as exc:
            kb = len(payload) / 1024
            return Err(self._sanitize(exc, context="call.error", payload_kb=kb))

    def _sanitize(
        self, exc: Exception, *, context: str = "", payload_kb: float = 0.0
    ) -> SanitizedError:
        name = type(exc).__name__
        if isinstance(exc, MaxPayloadError):
            return SanitizedError(
                code="transport.payload_too_large", message=name, retryable=False
            )
        code_map = {
            "TimeoutError": "transport.timeout",
            "NoRespondersError": "transport.no_responders",
        }
        code = code_map.get(name, "transport.error")
        return SanitizedError(code=code, message=name, retryable=True)

    @asynccontextmanager
    async def open_inbox(self) -> AsyncIterator[InboxStream]:
        inbox = self._nc.new_inbox()
        sub = await self._nc.subscribe(inbox)
        log.info("transport.inbox_open inbox=%s", inbox)

        async def _messages() -> AsyncIterator[Result[bytes, SanitizedError]]:
            try:
                while True:
                    try:
                        msg = await sub.next_msg(timeout=self._default_timeout)
                        yield Ok(msg.data)
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        yield Err(self._sanitize(exc, context="inbox.timeout"))
                        return
            except Exception as exc:  # noqa: BLE001 — sanitization barrier: any stream failure becomes Err
                yield Err(self._sanitize(exc, context="inbox.error"))

        try:
            yield InboxStream(inbox_subject=inbox, messages=_messages())
        finally:
            await sub.unsubscribe()
            log.info("transport.inbox_close inbox=%s", inbox)
