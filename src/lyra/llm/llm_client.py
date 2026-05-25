"""LlmClient — thin domain client over WorkerPoolClient + LlmCodec.

3-layer composition (spec § Slice S4):
  LlmClient → WorkerPoolClient → NatsTransport (or HttpTransport P4)

Implements the LlmProvider protocol over Result[T, SanitizedError] transport.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator, Protocol
from uuid import uuid4

from pydantic import ValidationError

from lyra.core.messaging.events import LlmEvent, ResultLlmEvent
from lyra.core.ports.llm import LlmResult
from lyra.transport._result import Ok
from roxabi_contracts.cli.models import CliControlAck, CliControlCmd
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.llm import SUBJECTS

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.core.agent.agent_config import ModelConfig
    from lyra.llm.codec import LlmCodec
    from lyra.transport.worker_pool_client import WorkerPoolClient

log = logging.getLogger(__name__)

_SUBJECT_CONTROL = "lyra.clipool.control"


class _CliSessionStore(Protocol):
    """Read-side protocol for TurnStore lookups needed by LlmClient.

    Writes (set_cli_session) flow through TurnPublisher → TurnWriter — they
    no longer live on this protocol.
    """

    async def get_cli_session(self, session_id: str) -> str | None: ...


class LlmClient:
    capabilities = {"streaming": True, "auth": "nats"}

    def __init__(
        self,
        pool: "WorkerPoolClient",
        codec: "LlmCodec",
        *,
        timeout: float | None = None,
        request_subject: str | None = None,
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._timeout = timeout
        self._request_subject = request_subject or SUBJECTS.generate_request
        self._lyra_sessions: dict[str, str] = {}
        self._turn_store: _CliSessionStore | None = None

    def is_alive(self, pool_id: str) -> bool:
        del pool_id
        return self._pool.is_pool_alive()

    async def stop(self) -> None:
        """Stop heartbeat subscription on the underlying pool."""
        await self._pool.stop()

    async def complete(  # noqa: PLR0913 — LlmProvider protocol signature
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        payload, trace_id = self._codec.encode(
            text,
            model_cfg,
            system_prompt,
            messages,
            stream=False,
            pool_id=pool_id,
            lyra_session_id=self._lyra_sessions.get(pool_id),
        )
        result = await self._pool.request_with_routing(
            lambda _: self._request_subject,
            payload,
            max_attempts=1,
            timeout=self._timeout,
        )
        return self._codec.decode(result, trace_id)

    async def stream(  # noqa: PLR0913 — LlmProvider protocol signature
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        payload, _ = self._codec.encode(
            text,
            model_cfg,
            system_prompt,
            messages,
            stream=True,
            pool_id=pool_id,
            lyra_session_id=self._lyra_sessions.get(pool_id),
        )
        async for result in self._pool.stream_request(
            self._request_subject, payload, timeout=self._timeout
        ):
            event = self._codec.decode_chunk(result)
            if event is None:
                continue
            yield event
            if isinstance(event, ResultLlmEvent):
                return

    # ── Control-plane methods (re-homed from CliNatsDriver, Slice S2) ────

    def set_turn_store(self, store: _CliSessionStore) -> None:
        """Wire the session store for cli_session_id lookups in resume_and_reset."""
        self._turn_store = store

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Store lyra_session_id for pool_id; encode uses it for envelope injection.
        Pure local mapping — no NATS message sent (mirrors CliNatsDriver behaviour).
        """
        self._lyra_sessions[pool_id] = lyra_session_id
        log.debug(
            "llm_client: link pool_id=%s → lyra_session=%s", pool_id, lyra_session_id
        )

    def unlink_lyra_session(self, pool_id: str) -> None:
        """Remove the pool_id → lyra_session_id mapping (call on pool eviction)."""
        self._lyra_sessions.pop(pool_id, None)

    async def reset(self, pool_id: str) -> None:
        """Send reset control command to clipool worker."""
        cmd = CliControlCmd(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            op="reset",
        )
        payload = self._codec.encode_control(cmd)
        await self._pool._transport.call(  # type: ignore[attr-defined]  # noqa: SLF001
            _SUBJECT_CONTROL, payload, timeout=self._timeout
        )

    async def resume_and_reset(self, pool_id: str, session_id: str) -> bool:
        """Ask clipool worker to resume a prior session then reset.

        Looks up cli_session_id from the hub TurnStore and passes it to the
        worker directly (no TurnStore lookup on the worker side).
        """
        cli_sid: str | None = None
        if self._turn_store is not None:
            cli_sid = await self._turn_store.get_cli_session(session_id)
        if cli_sid is None:
            log.debug(
                "llm_client: no cli_session_id for lyra_session=%s, "
                "skipping resume [pool:%s]",
                session_id,
                pool_id,
            )
            return False
        cmd = CliControlCmd(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            op="resume_and_reset",
            session_id=cli_sid,
        )
        payload = self._codec.encode_control(cmd)
        result = await self._pool._transport.call(  # type: ignore[attr-defined]  # noqa: SLF001
            _SUBJECT_CONTROL, payload, timeout=self._timeout
        )
        if not isinstance(result, Ok):
            return False
        try:
            ack = CliControlAck.model_validate_json(result.value)
            return bool(ack.resumed)
        except ValidationError as exc:
            log.warning("llm_client: CliControlAck parse failed: %r", exc)
            return False

    async def switch_cwd(self, pool_id: str, cwd: "Path") -> None:
        """Ask clipool worker to switch the working directory."""
        cmd = CliControlCmd(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            op="switch_cwd",
            cwd=str(cwd),
        )
        payload = self._codec.encode_control(cmd)
        await self._pool._transport.call(  # type: ignore[attr-defined]  # noqa: SLF001
            _SUBJECT_CONTROL, payload, timeout=self._timeout
        )
