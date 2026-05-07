"""NatsLlmClient — hub-side NATS client for LLM generation.

Maintains a ``WorkerRegistry`` populated from heartbeats. Publishes each
generation request to the canonical literal subject ``lyra.llm.generate.request``
— the NATS broker dispatches to a worker via the ``llm-workers`` queue group.

Per-worker score-routed subjects (``lyra.llm.generate.request.{worker_id}``)
were dropped in lyra#1104 to match the canonical ACL allow list. Score-routing
will return when finishing the LlmProvider conformance + bootstrap migration
tracked in lyra#1119.

Implements the ``LlmProvider`` protocol — future replacement candidate for
``NatsLlmDriver`` using ADR-049 Pydantic contracts.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, NoReturn
from uuid import uuid4

import nats.errors
from nats.aio.client import Client as NATS
from nats.errors import NoRespondersError
from pydantic import ValidationError

import nats
from lyra.core.ports.llm import LlmUnavailableError
from lyra.nats.worker_registry import WorkerRegistry
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.llm import (
    SUBJECTS,
    LlmChunkEvent,
    LlmRequest,
    LlmResponse,
    validate_worker_id,
)
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

log = logging.getLogger(__name__)

_TIMEOUT_DEFAULT = 120.0
_TIMEOUT_MIN = 5.0
_TIMEOUT_MAX = 600.0


def _parse_llm_timeout(timeout: float | None) -> float:
    if timeout is not None:
        value = timeout
    else:
        raw = os.environ.get("LYRA_LLM_TIMEOUT", str(_TIMEOUT_DEFAULT))
        try:
            value = float(raw)
        except ValueError:
            log.warning(
                "LYRA_LLM_TIMEOUT=%r is not a valid float; using %.0fs",
                raw,
                _TIMEOUT_DEFAULT,
            )
            return _TIMEOUT_DEFAULT
    if not (_TIMEOUT_MIN <= value <= _TIMEOUT_MAX):
        log.warning(
            "LLM timeout %.1fs out of range [%.0f, %.0f]; using %.0fs",
            value,
            _TIMEOUT_MIN,
            _TIMEOUT_MAX,
            _TIMEOUT_DEFAULT,
        )
        return _TIMEOUT_DEFAULT
    return value


class NatsLlmClient:
    """Hub-side NATS client for LLM generation.

    Usage::

        client = NatsLlmClient(nc)
        await client.start()

        # streaming
        async for delta in client.stream(messages=[...]):
            print(delta, end="", flush=True)

        # non-streaming
        result = await client.generate(messages=[...])

        await client.stop()
    """

    def __init__(self, nc: NATS, *, timeout: float | None = None) -> None:
        self._nc = nc
        self._timeout = _parse_llm_timeout(timeout)
        self._cb = NatsCircuitBreaker()
        self._registry = WorkerRegistry()
        self._hb_sub = None

    async def start(self) -> None:
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.heartbeat, cb=self._on_heartbeat
            )

    async def stop(self) -> None:
        if self._hb_sub is not None:
            await self._hb_sub.unsubscribe()
            self._hb_sub = None

    def is_available(self) -> bool:
        return self._registry.any_alive()

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            log.debug("llm_client: heartbeat parse error", exc_info=True)
            return
        worker_id = data.get("worker_id")
        if not worker_id or not isinstance(worker_id, str):
            log.warning("llm_client: heartbeat missing/invalid worker_id, ignoring")
            return
        try:
            validate_worker_id(worker_id)
        except ValueError:
            log.warning(
                "llm_client: heartbeat unsafe worker_id=%r, ignoring", worker_id
            )
            return
        self._registry.record_heartbeat(data)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Non-streaming generation. Returns full text string."""
        if self._cb.is_open():
            raise LlmUnavailableError(
                "LLM circuit open — adapter temporarily unavailable"
            )

        request = LlmRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()).replace("-", "")[:32],
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        payload = request.model_dump_json(exclude_none=True).encode()
        resp = await self._walk_registry_request(payload)
        assert resp.text is not None
        self._cb.record_success()
        return resp.text

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Streaming generation. Yields text deltas."""
        if self._cb.is_open():
            raise LlmUnavailableError(
                "LLM circuit open — adapter temporarily unavailable"
            )

        request = LlmRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()).replace("-", "")[:32],
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        payload = request.model_dump_json(exclude_none=True).encode()
        return self._stream_from_worker(payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _stream_from_worker(self, payload: bytes) -> AsyncIterator[str]:
        candidates = self._registry.ordered_by_score()
        if not candidates:
            raise LlmUnavailableError("LLM: no live worker (heartbeat stale >15s)")

        inbox = self._nc.new_inbox()
        sub = await self._nc.subscribe(inbox)
        try:
            target = SUBJECTS.generate_request
            await self._nc.publish(target, payload, reply=inbox)
            while True:
                try:
                    msg = await sub.next_msg(timeout=self._timeout)
                except Exception as exc:
                    self._registry.mark_stale(candidates[0].worker_id)
                    self._cb.record_failure()
                    raise LlmUnavailableError("LLM stream timed out") from exc
                try:
                    chunk = LlmChunkEvent.model_validate_json(msg.data)
                except (ValidationError, ValueError) as exc:
                    self._cb.record_failure()
                    raise LlmUnavailableError("LLM stream: malformed chunk") from exc
                if chunk.is_error:
                    self._cb.record_failure()
                    raise LlmUnavailableError(chunk.error or "LLM stream error")
                if chunk.delta:
                    yield chunk.delta
                if chunk.done:
                    self._cb.record_success()
                    return
        finally:
            await sub.unsubscribe()

    async def _walk_registry_request(self, payload: bytes) -> LlmResponse:
        candidates = self._registry.ordered_by_score()
        if not candidates:
            raise LlmUnavailableError("LLM: no live worker (heartbeat stale >15s)")

        last_exc: Exception | None = None
        for worker in candidates:
            target = SUBJECTS.generate_request
            try:
                reply = await self._nc.request(target, payload, timeout=self._timeout)
                resp = LlmResponse.model_validate_json(reply.data)
                if not resp.ok:
                    self._cb.record_failure()
                    raise LlmUnavailableError(resp.error or "LLM generation failed")
                return resp
            except TimeoutError as exc:
                self._registry.mark_stale(worker.worker_id)
                last_exc = exc
                continue
            except (nats.errors.Error, TimeoutError) as exc:
                if isinstance(exc, NoRespondersError):
                    self._registry.mark_stale(worker.worker_id)
                    last_exc = exc
                    continue
                self._raise_nats_failure(exc, len(payload) / 1024)

        self._cb.record_failure()
        raise LlmUnavailableError("LLM: all workers unresponsive") from last_exc

    def _parse_reply(self, data: bytes) -> LlmResponse:
        try:
            return LlmResponse.model_validate_json(data)
        except (ValidationError, ValueError) as exc:
            self._cb.record_failure()
            raise LlmUnavailableError("LLM reply failed schema validation") from exc

    def _raise_nats_failure(self, exc: Exception, payload_kb: float) -> NoReturn:
        if isinstance(exc, LlmUnavailableError):
            raise exc
        if "max_payload" in str(exc).lower():
            log.error("LLM payload too large (%.0f KB)", payload_kb)
            self._cb.record_failure()
            raise LlmUnavailableError("LLM request payload too large") from exc
        log.warning("LLM adapter unreachable: %s: %s", type(exc).__name__, exc)
        self._cb.record_failure()
        raise LlmUnavailableError("LLM adapter unreachable") from exc
