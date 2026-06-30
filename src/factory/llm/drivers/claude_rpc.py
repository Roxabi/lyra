"""ClaudeRpcDriver — LlmProvider over NATS clipool job pub/sub (phase 2).

Publishes JobEnvelope to ``factory.jobs.claude`` and consumes
``factory.job.<id>.{progress,result}`` — same calling model as OmpRpcDriver.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

from pydantic import ValidationError

from factory.core.agent.agent_config import ModelConfig
from factory.core.envelope_fields import control_trace_id, mint_work_envelope_fields
from factory.core.messaging.events import LlmEvent, ResultLlmEvent
from factory.core.ports.llm import LlmResult
from factory.core.trace import TraceContext
from factory.llm.claude_job_codec import ClaudeJobCodec
from factory.obs.hub_tracer import nats_client_span
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.jobs import JobEnvelope, JobProgress, JobResult
from roxabi_contracts.jobs.subjects import jobs_progress, jobs_result, jobs_submit

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S: float = 600.0


class _ClaudeSessionStore(Protocol):
    async def get_cli_session(self, session_id: str) -> str | None: ...

    async def _set_cli_session(self, session_id: str, cli_session_id: str) -> None: ...


class ClaudeRpcDriver:
    """LlmProvider adapter for the clipool NATS worker (JobEnvelope + pub/sub)."""

    capabilities: dict = {"streaming": True}

    def __init__(
        self,
        nc: Any,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        codec: ClaudeJobCodec | None = None,
    ) -> None:
        self._nc = nc
        self._timeout_s = timeout_s
        self._codec = codec or ClaudeJobCodec()
        self._turn_store: _ClaudeSessionStore | None = None
        self._pending_resume: dict[str, str] = {}
        self._lyra_sessions: dict[str, str] = {}

    def is_alive(self, pool_id: str) -> bool:  # noqa: ARG002
        return True

    async def stop(self) -> None:
        """Lifecycle hook — no heartbeat subscription in phase 2."""

    def set_turn_store(self, store: _ClaudeSessionStore) -> None:
        self._turn_store = store

    def link_lyra_session(self, pool_id: str, session_id: str) -> None:
        self._lyra_sessions[pool_id] = session_id
        log.debug("claude_rpc: link pool_id=%s → lyra_session=%s", pool_id, session_id)

    def unlink_lyra_session(self, pool_id: str) -> None:
        self._lyra_sessions.pop(pool_id, None)

    async def reset(self, pool_id: str) -> None:
        """Drop pending resume; reset via factory.clipool.control."""
        self._pending_resume.pop(pool_id, None)
        self._lyra_sessions.pop(pool_id, None)
        from roxabi_contracts.cli import SUBJECTS as CLI_SUBJECTS
        from roxabi_contracts.cli.models import CliControlCmd
        from roxabi_contracts.envelope import CONTRACT_VERSION

        cmd = CliControlCmd(
            contract_version=CONTRACT_VERSION,
            trace_id=control_trace_id(),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            op="reset",
        )
        await self._nc.request(
            CLI_SUBJECTS.control,
            cmd.model_dump_json(exclude_none=True).encode(),
            timeout=30.0,
        )

    async def queue_resume(self, pool_id: str, session_id: str) -> bool:
        cli_sid: str | None = None
        if self._turn_store is not None:
            cli_sid = await self._turn_store.get_cli_session(session_id)
        if cli_sid is None:
            log.debug(
                "claude_rpc: no cli_session_id for lyra_session=%s, skipping resume",
                session_id,
            )
            return False
        self._pending_resume[pool_id] = cli_sid
        return True

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        del messages
        pending_resume = self._pending_resume.pop(pool_id, None)
        fields = mint_work_envelope_fields(
            trace_id=TraceContext.get_trace_id() or TraceContext.generate(),
            pool_id=pool_id,
        )
        job_id = fields.job_id
        result_sub = await self._nc.subscribe(jobs_result(job_id))
        try:
            await self._publish_envelope(
                job_id,
                pool_id=pool_id,
                text=text,
                model_cfg=model_cfg,
                system_prompt=system_prompt,
                stream=False,
                provider_session_id=pending_resume,
            )
            msg = await result_sub.next_msg(timeout=self._timeout_s)
            try:
                result = JobResult.model_validate_json(msg.data)
            except ValidationError:
                log.warning("claude job %s returned a malformed result", job_id)
                return self._codec.decode_malformed()

            decoded = self._codec.decode_result(result)
            if result.status == "success":
                await self._persist_session(pool_id, result)
            return decoded
        except (TimeoutError, asyncio.TimeoutError):
            log.warning("claude job %s timed out after %.1fs", job_id, self._timeout_s)
            return self._codec.decode_timeout()
        finally:
            await result_sub.unsubscribe()

    async def stream(  # noqa: C901, PLR0915
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        del messages
        pending_resume = self._pending_resume.pop(pool_id, None)
        fields = mint_work_envelope_fields(
            trace_id=TraceContext.get_trace_id() or TraceContext.generate(),
            pool_id=pool_id,
        )
        job_id = fields.job_id
        progress_sub = await self._nc.subscribe(jobs_progress(job_id))
        result_sub = await self._nc.subscribe(jobs_result(job_id))
        queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

        async def _pump_progress() -> None:
            try:
                while True:
                    msg = await progress_sub.next_msg(timeout=self._timeout_s)
                    await queue.put(("progress", msg.data))
            except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
                return

        async def _pump_result() -> None:
            msg = await result_sub.next_msg(timeout=self._timeout_s)
            await queue.put(("result", msg.data))

        progress_task = asyncio.create_task(_pump_progress())
        result_task = asyncio.create_task(_pump_result())

        try:
            await self._publish_envelope(
                job_id,
                pool_id=pool_id,
                text=text,
                model_cfg=model_cfg,
                system_prompt=system_prompt,
                stream=True,
                provider_session_id=pending_resume,
            )

            got_result = False
            while not got_result:
                kind, data = await asyncio.wait_for(
                    queue.get(), timeout=self._timeout_s
                )
                if kind == "progress":
                    try:
                        progress = JobProgress.model_validate_json(data)
                    except ValidationError:
                        log.warning("claude job %s malformed progress", job_id)
                        continue
                    event = self._codec.decode_progress(progress)
                    if event is not None:
                        yield event
                else:
                    got_result = True
                    try:
                        result = JobResult.model_validate_json(data)
                    except ValidationError:
                        yield self._codec.progress_to_terminal(
                            JobResult.model_validate(
                                {
                                    "contract_version": CONTRACT_VERSION,
                                    "trace_id": job_id,
                                    "issued_at": datetime.now(timezone.utc),
                                    "job_id": job_id,
                                    "status": "error",
                                    "error": {
                                        "code": "transport.parse",
                                        "message": "malformed result",
                                        "retryable": False,
                                    },
                                }
                            )
                        )
                        return
                    if result.status == "success":
                        await self._persist_session(pool_id, result)
                    terminal = self._codec.progress_to_terminal(result)
                    yield terminal
                    return
        except (TimeoutError, asyncio.TimeoutError):
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=self._codec.decode_timeout().error,
            )
        finally:
            progress_task.cancel()
            result_task.cancel()
            await asyncio.gather(progress_task, result_task, return_exceptions=True)
            await progress_sub.unsubscribe()
            await result_sub.unsubscribe()

    async def _publish_envelope(  # noqa: PLR0913
        self,
        job_id: str,
        *,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        stream: bool,
        provider_session_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "prompt": text,
            "model_cfg": model_cfg.model_dump(),
            "system_prompt": system_prompt,
            "pool_id": pool_id,
            "stream": stream,
            "lyra_session_id": self._lyra_sessions.get(pool_id) or pool_id,
            "agent_name": TraceContext.get_agent_name(),
        }
        if provider_session_id is not None:
            payload["provider_session_id"] = provider_session_id

        fields = mint_work_envelope_fields(
            trace_id=TraceContext.get_trace_id() or TraceContext.generate(),
            job_id=job_id,
            pool_id=pool_id,
        )
        env = JobEnvelope(
            contract_version=fields.contract_version,
            trace_id=fields.trace_id,
            issued_at=fields.issued_at,
            job_id=job_id,
            job_name="claude",
            payload=payload,
            reply_to=f"_INBOX.{job_id}",
        )
        wire = env.model_dump_json().encode()
        submit_subject = jobs_submit("claude")
        with nats_client_span(
            name="claude",
            subject=submit_subject,
            payload=wire,
            trace_id=fields.trace_id,
            job_id=job_id,
            pool_id=pool_id,
        ):
            await self._nc.publish(submit_subject, wire)

    async def _persist_session(self, pool_id: str, result: JobResult) -> None:
        data = result.data or {}
        session_id = data.get("session_id")
        if not session_id or self._turn_store is None:
            return
        linked = self._lyra_sessions.get(pool_id)
        if linked is None:
            return
        await self._turn_store._set_cli_session(linked, str(session_id))  # noqa: SLF001