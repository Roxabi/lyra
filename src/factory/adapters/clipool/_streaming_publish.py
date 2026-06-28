"""Publish clipool streaming events as JobProgress / JobResult (phase 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from factory.adapters.clipool.error_classifier import worker_error_from_cli_result
from factory.adapters.omp._rpc_envelope import make_progress, make_result
from factory.core.cli.cli_pool import CliResult
from factory.core.messaging.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs.subjects import jobs_progress, jobs_result


async def publish_streaming_job(
    nc: Any,
    *,
    job_id: str,
    iterator: AsyncIterator[Any],
    resumed: bool | None = None,
) -> None:
    """Map pool LlmEvents to JobProgress publishes; terminal JobResult at end."""
    first = True
    async for event in iterator:
        if isinstance(event, TextLlmEvent):
            detail = {"resumed": resumed} if first and resumed is not None else None
            first = False
            await nc.publish(
                jobs_progress(job_id),
                make_progress(
                    job_id,
                    step="text",
                    event_type="text",
                    partial_text=event.text,
                    detail=detail,
                ),
            )
        elif isinstance(event, ToolUseLlmEvent):
            await nc.publish(
                jobs_progress(job_id),
                make_progress(
                    job_id,
                    step="tool",
                    event_type="tool_use",
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    tool_input=event.input,
                ),
            )
        elif isinstance(event, ResultLlmEvent):
            await _publish_terminal(
                nc,
                job_id=job_id,
                is_error=event.is_error,
                text=None,
                session_id=event.session_id,
                worker_error=event.worker_error,
                resumed=resumed if first else None,
            )
            return

    await _publish_terminal(nc, job_id=job_id, is_error=False, text="", session_id=None)


async def publish_blocking_result(
    nc: Any,
    *,
    job_id: str,
    result: CliResult,
    resumed: bool | None = None,
) -> None:
    """Publish a single JobResult for a non-streaming clipool turn."""
    worker_error = (
        worker_error_from_cli_result(result.error) if result.error else None
    )
    await _publish_terminal(
        nc,
        job_id=job_id,
        is_error=bool(result.error),
        text=result.result or None,
        session_id=result.session_id or None,
        worker_error=worker_error,
        resumed=resumed,
    )


async def publish_job_failure(
    nc: Any,
    *,
    job_id: str,
    exc: BaseException,
) -> None:
    """Publish JobResult(status=error) for an unhandled worker failure."""
    from factory.adapters.clipool.error_classifier import classify_exception

    worker_error = classify_exception(exc)
    await nc.publish(
        jobs_result(job_id),
        make_result(job_id, status="error", error=worker_error),
    )


async def _publish_terminal(  # noqa: PLR0913
    nc: Any,
    *,
    job_id: str,
    is_error: bool,
    text: str | None,
    session_id: str | None = None,
    worker_error: WorkerError | None = None,
    resumed: bool | None = None,
) -> None:
    if is_error or worker_error is not None:
        validated = worker_error or WorkerError(
            code="worker.internal",
            message="LLM generation failed",
            retryable=True,
        )
        await nc.publish(
            jobs_result(job_id),
            make_result(job_id, status="error", error=validated),
        )
        return

    data: dict[str, Any] = {}
    if text:
        data["result"] = text
    if session_id:
        data["session_id"] = session_id
    if resumed is not None:
        data["resumed"] = resumed

    await nc.publish(
        jobs_result(job_id),
        make_result(job_id, status="success", data=data or None),
    )