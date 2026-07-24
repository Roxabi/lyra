"""OmpRpcDriver — LlmProvider over NATS omp job round-trip.

Implements the LlmProvider protocol by publishing a JobEnvelope to
``factory.jobs.omp`` and awaiting a JobResult on the per-job reply subject.

# NOTE: contracts convention prefers ``roxabi_nats.deserialize()`` for the
# 1 MB gate and envelope validation.  V1 uses direct ``model_validate_json``
# on the hub-controlled reply path (short-circuit, trusted channel) — flag
# for review/V2 hardening.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from pydantic import ValidationError

from factory.core.agent.agent_config import ModelConfig
from factory.core.envelope_fields import mint_work_envelope_fields
from factory.core.ports.llm import LlmResult
from factory.llm.omp_job_codec import OmpJobCodec
from factory.obs.hub_tracer import nats_client_span
from roxabi_contracts.jobs import JobEnvelope, JobResult
from roxabi_contracts.jobs.subjects import jobs_result, jobs_submit

log = logging.getLogger(__name__)

# Default wall-clock budget for a single omp job round-trip (seconds).
# omp jobs can be long-running LLM calls; 600 s matches the NatsTransport
# per-chunk liveness default used by LlmClient on the clipool path.
_DEFAULT_TIMEOUT_S: float = 600.0


class _OmpSessionStore(Protocol):
    """Read-side only — hub mounts turns.db RO (ADR-075); writes use TurnPublisher."""

    async def get_cli_session(self, session_id: str) -> str | None: ...


class _OmpTurnPublisher(Protocol):
    """Structural subset of TurnPublisher (avoids llm→transport hard import)."""

    async def publish_set_cli_session(  # noqa: PLR0913 — matches TurnPublisher wire
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        cli_session_id: str,
        trace_id: str,
    ) -> None: ...


class OmpRpcDriver:
    """LlmProvider adapter that dispatches work to the omp NATS worker.

    The hub mints the job_id (omp never mints its own).  The driver
    subscribes to the reply subject *before* publishing to avoid a
    race between the worker reply and the subscribe call.

    SessionAware protocol (V2)
    --------------------------
    Mirrors LlmClient for session management:
      - set_turn_store(store)        — RO lookups (get_cli_session)
      - set_turn_publisher(pub)      — set_cli_session writes via NATS
      - link_lyra_session(p, s)      — local mapping pool_id → session_id
      - queue_resume(p, s) -> bool   — resolve + stash cli_session_id
      - reset(pool_id)               — drop pending resume (no NATS msg in V2)
    """

    capabilities: dict = {"streaming": False}

    def __init__(
        self,
        nc: Any,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        codec: OmpJobCodec | None = None,
    ) -> None:
        # raw async NATS connection (nats-py), injected at bootstrap (T11)
        self._nc = nc
        self._timeout_s = timeout_s
        self._codec = codec or OmpJobCodec()
        # SessionAware state (mirrors LlmClient.__init__)
        self._turn_store: _OmpSessionStore | None = None
        self._turn_publisher: _OmpTurnPublisher | None = None
        self._pending_resume: dict[str, str] = {}
        self._lyra_sessions: dict[str, str] = {}

    def is_alive(self, pool_id: str) -> bool:  # noqa: ARG002
        """V1: always reports alive — no heartbeat mechanism yet."""
        return True

    # ── SessionAware protocol ──────────────────────────────────────────────

    def set_turn_store(self, store: _OmpSessionStore) -> None:
        """Wire the session store for cli_session_id lookups in queue_resume."""
        self._turn_store = store

    def set_turn_publisher(self, publisher: _OmpTurnPublisher) -> None:
        """Wire TurnPublisher for set_cli_session (hub must not SQLite-write)."""
        self._turn_publisher = publisher

    def link_lyra_session(self, pool_id: str, session_id: str) -> None:
        """Store the pool_id → session_id mapping for session persistence.

        Pure local mutation — no NATS message sent (mirrors LlmClient).
        """
        self._lyra_sessions[pool_id] = session_id
        log.debug("omp_rpc: link pool_id=%s → lyra_session=%s", pool_id, session_id)

    async def reset(self, pool_id: str) -> None:
        """Drop any pending resume token for pool_id.

        V2: no NATS control message is sent to the omp worker because there
        is no per-worker control path in this version.  OmpPool cold-starts
        the next turn automatically when no provider_session_id is present in
        the JobEnvelope payload.
        """
        self._pending_resume.pop(pool_id, None)
        self._lyra_sessions.pop(pool_id, None)

    async def queue_resume(self, pool_id: str, session_id: str) -> bool:
        """Resolve cli_session_id and stash it for the next complete() call.

        Looks up cli_session_id from the hub TurnStore and stores it in
        _pending_resume[pool_id].  The stashed token is injected into the next
        JobEnvelope.payload as ``provider_session_id`` — no separate NATS
        control message is sent.

        Returns True iff a cli_session_id was resolved and stashed.  True does
        NOT mean the resume has been applied — the omp worker applies it on
        the next job it receives.

        Mirrors LlmClient.queue_resume exactly.
        """
        cli_sid: str | None = None
        if self._turn_store is not None:
            cli_sid = await self._turn_store.get_cli_session(session_id)
        if cli_sid is None:
            log.debug(
                "omp_rpc: no cli_session_id for lyra_session=%s, "
                "skipping resume [pool:%s]",
                session_id,
                pool_id,
            )
            return False
        self._pending_resume[pool_id] = cli_sid
        return True

    # ── LlmProvider protocol ───────────────────────────────────────────────

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,  # protocol compliance; omp owns its session
    ) -> LlmResult:
        """Dispatch a prompt to omp via NATS and return the result.

        ``messages`` is accepted for LlmProvider protocol compliance but
        ignored in V1: omp manages its own session history internally.

        If a pending resume token exists for pool_id, it is injected into the
        JobEnvelope payload as ``provider_session_id`` so the omp worker can
        resume the previous session.  On success, if the worker returns a
        ``session_file`` in JobResult.data and a lyra session_id is linked,
        the file path is persisted via the TurnStore write path.
        """
        del messages  # omp uses its own session

        # Pop pending resume before publish; do NOT re-stash on failure (omp
        # is stateless per-job — a failed job does not consume the session).
        pending_resume = self._pending_resume.pop(pool_id, None)

        fields = mint_work_envelope_fields(

            pool_id=pool_id,
        )
        job_id = fields.job_id
        result_subject = jobs_result(job_id)
        submit_subject = jobs_submit("omp")

        # Subscribe BEFORE publish to avoid missing the reply.
        sub = await self._nc.subscribe(result_subject)
        try:
            payload: dict[str, Any] = {
                "prompt": text,
                "model_cfg": model_cfg.model_dump(),
                "system_prompt": system_prompt,
                "pool_id": pool_id,
            }
            if pending_resume is not None:
                payload["provider_session_id"] = pending_resume

            env = JobEnvelope(
                contract_version=fields.contract_version,
                trace_id=fields.trace_id,
                issued_at=fields.issued_at,
                job_id=job_id,
                job_name="omp",
                payload=payload,
                # reply_to satisfies the JobEnvelope contract validator (requires
                # _INBOX.*/_R_.*) but is unused for routing: the omp worker publishes
                # the JobResult to the deterministic jobs_result(job_id) subject we
                # subscribed to above, not to reply_to. Kept for envelope validity.
                reply_to=f"_INBOX.{job_id}",
            )
            wire = env.model_dump_json().encode()
            with nats_client_span(
                name="omp",
                subject=submit_subject,
                payload=wire,
                trace_id=fields.trace_id,
                job_id=job_id,
                pool_id=pool_id,
            ):
                await self._nc.publish(submit_subject, wire)

            msg = await sub.next_msg(timeout=self._timeout_s)
            try:
                result = JobResult.model_validate_json(msg.data)
            except ValidationError:
                log.warning("omp job %s returned a malformed result", job_id)
                return self._codec.decode_malformed()

            decoded = self._codec.decode(result)
            if result.status == "success":
                session_file = (result.data or {}).get("session_file")
                if session_file:
                    await self._persist_session(pool_id, str(session_file))
                return decoded

            log.warning("omp worker returned error status for job %s", job_id)
            return decoded

        except (TimeoutError, asyncio.TimeoutError):
            log.warning("omp job %s timed out after %.1fs", job_id, self._timeout_s)
            return self._codec.decode_timeout()

        finally:
            await sub.unsubscribe()

    async def _persist_session(self, pool_id: str, session_file: str) -> None:
        """Publish set_cli_session via TurnPublisher (ADR-075 sole writer)."""
        linked_session = self._lyra_sessions.get(pool_id)
        if linked_session is None:
            return
        publisher = self._turn_publisher
        if publisher is None:
            log.warning(
                "omp_rpc: turn_publisher unset — drop set_cli_session "
                "for lyra_session=%s (hub RO turns.db)",
                linked_session,
            )
            return
        await publisher.publish_set_cli_session(
            pool_id=pool_id,
            session_id=linked_session,
            platform="",
            user_id="",
            cli_session_id=session_file,
            trace_id=linked_session,
        )
