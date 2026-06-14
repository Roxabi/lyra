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
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from factory.core.agent.agent_config import ModelConfig
from factory.core.ports.llm import LlmResult
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.jobs import JobEnvelope, JobResult
from roxabi_contracts.jobs.subjects import jobs_result, jobs_submit

log = logging.getLogger(__name__)

# Default wall-clock budget for a single omp job round-trip (seconds).
# omp jobs can be long-running LLM calls; 600 s matches the NatsTransport
# per-chunk liveness default used by LlmClient on the clipool path.
_DEFAULT_TIMEOUT_S: float = 600.0


class OmpRpcDriver:
    """LlmProvider adapter that dispatches work to the omp NATS worker.

    The hub mints the job_id (omp never mints its own).  The driver
    subscribes to the reply subject *before* publishing to avoid a
    race between the worker reply and the subscribe call.
    """

    capabilities: dict = {"streaming": False}

    def __init__(self, nc: Any, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        # raw async NATS connection (nats-py), injected at bootstrap (T11)
        self._nc = nc
        self._timeout_s = timeout_s

    def is_alive(self, pool_id: str) -> bool:  # noqa: ARG002
        """V1: always reports alive — no heartbeat mechanism yet."""
        return True

    async def complete(
        self,
        pool_id: str,  # noqa: ARG002
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,  # protocol compliance; omp owns its session
    ) -> LlmResult:
        """Dispatch a prompt to omp via NATS and return the result.

        ``messages`` is accepted for LlmProvider protocol compliance but
        ignored in V1: omp manages its own session history internally.
        """
        del messages  # omp uses its own session

        job_id = uuid4().hex
        result_subject = jobs_result(job_id)

        # Subscribe BEFORE publish to avoid missing the reply.
        sub = await self._nc.subscribe(result_subject)
        try:
            env = JobEnvelope(
                contract_version=CONTRACT_VERSION,
                trace_id=job_id,
                issued_at=datetime.now(tz=timezone.utc),
                job_id=job_id,
                job_name="omp",
                payload={
                    "prompt": text,
                    "model_cfg": model_cfg.model_dump(),
                    "system_prompt": system_prompt,
                },
                # reply_to satisfies the JobEnvelope contract validator (requires
                # _INBOX.*/_R_.*) but is unused for routing: the omp worker publishes
                # the JobResult to the deterministic jobs_result(job_id) subject we
                # subscribed to above, not to reply_to. Kept for envelope validity.
                reply_to=f"_INBOX.{job_id}",
            )
            await self._nc.publish(
                jobs_submit("omp"),
                env.model_dump_json().encode(),
            )

            msg = await sub.next_msg(timeout=self._timeout_s)
            try:
                result = JobResult.model_validate_json(msg.data)
            except ValidationError:
                log.warning("omp job %s returned a malformed result", job_id)
                return LlmResult(
                    error="omp returned a malformed result", retryable=False
                )

            if result.status == "success":
                return LlmResult(result=(result.data or {}).get("result", ""))

            log.warning("omp worker returned error status for job %s", job_id)
            return LlmResult(error="omp worker returned an error", retryable=True)

        except (TimeoutError, asyncio.TimeoutError):
            log.warning("omp job %s timed out after %.1fs", job_id, self._timeout_s)
            return LlmResult(error="omp request timed out", retryable=True)

        finally:
            await sub.unsubscribe()
