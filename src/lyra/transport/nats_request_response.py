"""NatsTransport — request/response + inbox primitives over NATS.

Owns NATS-specific resources (Client ref, inbox lifecycle, sanitization).
Spec: artifacts/specs/1278-nats-transport-workerpool-spec.mdx (S2).
Consensus: artifacts/analyses/1278-nats-transport-workerpool-consensus.mdx (B1 hybrid).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.errors import NoRespondersError

from lyra.transport._result import Err, Ok, Result, SanitizedError

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


class NatsTransport:
    def __init__(self, nc: "NATS", *, default_timeout: float = 120.0) -> None:
        self._nc = nc
        self._default_timeout = default_timeout

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
        if "max_payload" in str(exc).lower():
            return SanitizedError(
                code="transport.payload_too_large", message=name, retryable=False
            )
        code_map = {
            "TimeoutError": "transport.timeout",
            "NoRespondersError": "transport.no_responders",
        }
        code = code_map.get(name, "transport.error")
        return SanitizedError(code=code, message=name, retryable=True)
