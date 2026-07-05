"""Dispatch helpers extracted from pool_processor_exec (issue #753)."""

from __future__ import annotations

import asyncio
import collections.abc
import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage
    from .pool import Pool

from factory.core.provider_match import is_provider_error

from ..messaging.message import OutboundMessage, Response
from ..messaging.utils.callbacks import TrustedCallback
from .pool_observer import TurnLogDeps
from .pool_processor_streaming import (
    StreamLogDeps,
    build_streaming_capture,
    build_streaming_turn_logger,
    run_streaming_turn_post,
)

log = logging.getLogger(__name__)

_POOL_TURN_ERRORS: tuple[type[BaseException], ...] = (
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    OSError,
    ConnectionError,
)
_PROCESSOR_HOOK_ERRORS: tuple[type[BaseException], ...] = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
)
_DISPATCH_ERRORS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    KeyError,
    OSError,
    ConnectionError,
    RuntimeError,
)


async def safe_dispatch(msg: InboundMessage, response: Response, pool: Pool) -> None:
    """Dispatch with timeout and error handling."""
    try:
        await asyncio.wait_for(
            pool._ctx.dispatch_response(msg, response),
            timeout=pool._safe_dispatch_timeout,
        )
    except _DISPATCH_ERRORS as exc:
        log.exception("safe_dispatch failed for pool %s: %s", pool.pool_id, exc)


async def run_processor_pre(
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> tuple[InboundMessage | None, object]:
    """Run processor pre-hook (B1 — #363)."""
    if msg.command is None:
        return msg, None
    _session_tools = getattr(agent, "_session_tools", None)
    if _session_tools is None:
        return msg, None
    importlib.import_module("factory.core.processors")
    from factory.core.processors.processor_registry import registry as _proc_registry

    _cmd_name = f"{msg.command.prefix}{msg.command.name}"
    _processor = _proc_registry.build(_cmd_name, _session_tools)
    if _processor is None:
        return msg, None
    try:
        msg = await _processor.pre(msg)
    except _PROCESSOR_HOOK_ERRORS:
        log.warning("Processor pre() failed for %s", _cmd_name, exc_info=True)
        _error_reply = pool._msg(
            "generic", f"Command {_cmd_name} failed to prepare. Please try again."
        )
        await safe_dispatch(msg, Response(content=_error_reply), pool)
        return None, None
    return msg, _processor


async def resolve_non_streaming(
    result: object, processor: object | None, original_msg: InboundMessage, pool: Pool
) -> object:
    """Await coroutine result and run processor post-hook if applicable."""
    try:
        result = await result  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    except _POOL_TURN_ERRORS as exc:
        pool._ctx.record_circuit_failure(exc)
        raise
    if processor is not None and isinstance(result, Response):
        try:
            result = await processor.post(original_msg, result)  # type: ignore[union-attr] — DEBT:defensive-narrow-payloads
        except _PROCESSOR_HOOK_ERRORS:
            log.warning("Processor post() failed", exc_info=True)
    return result


async def process_non_streaming(
    result: object,
    original_msg: InboundMessage,
    platform: str,
    user_id: str,
    pool: Pool,
) -> None:
    """Dispatch non-streaming Response: circuit success, session_id, turn-log."""
    pool._ctx.record_circuit_success()
    if isinstance(result, Response):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        await _update_session_id(result, pool)
        _capture_turn_log(
            result, platform, user_id, pool, root_job_id=original_msg.root_job_id
        )
    await pool._ctx.dispatch_response(original_msg, result)  # type: ignore[arg-type] — DEBT:defensive-narrow-payloads
    await pool._observer.session_update_async(original_msg)


async def process_streaming(  # noqa: PLR0913 — 6 cohesive params, no natural grouping
    result: collections.abc.AsyncIterator,
    processor: object | None,
    original_msg: InboundMessage,
    platform: str,
    user_id: str,
    pool: Pool,
) -> None:
    """Dispatch streaming result: capture, turn-log callback, circuit, post-hook."""
    _iter = result
    _content_parts: list[str] = []
    _stream_done = asyncio.Event() if processor is not None else None
    result = build_streaming_capture(_iter, _content_parts, pool, _stream_done)
    _outbound, _log_callback = build_streaming_turn_logger(
        StreamLogDeps(
            pool=pool,
            result_iter_for_sid=_iter,
            original_msg=original_msg,
            platform=platform,
            user_id=user_id,
            content_parts=_content_parts,
        )
    )
    pool._inflight_stream_outbound = _outbound
    _outbound.metadata["_on_dispatched"] = TrustedCallback(_log_callback)
    try:
        await pool._ctx.dispatch_streaming(original_msg, result, _outbound)
        pool._ctx.record_circuit_success()
    except BaseException as exc:
        pool._ctx.record_circuit_failure(exc)
        raise
    _stream_sid = getattr(_iter, "session_id", None)
    if _stream_sid and pool.session_id != _stream_sid:
        await pool._observer.end_session_async(pool.session_id)
        pool.session_id = _stream_sid
    await run_streaming_turn_post(processor, _stream_done, original_msg, _content_parts)


async def _update_session_id(result: Response, pool: Pool) -> None:
    _cli_session_id = result.metadata.get("session_id")
    if _cli_session_id:
        if pool.session_id != _cli_session_id:
            await pool._observer.end_session_async(pool.session_id)
        pool.session_id = _cli_session_id


def _capture_turn_log(
    result: Response,
    platform: str,
    user_id: str,
    pool: Pool,
    root_job_id: str | None = None,
) -> None:
    _content = result.content

    async def _log_turn(outbound: OutboundMessage) -> None:
        _reply_id = outbound.metadata.get("reply_message_id")
        await pool._observer.log_turn_async(
            TurnLogDeps(
                role="assistant",
                platform=platform,
                user_id=user_id,
                content=_content,
                reply_message_id=(str(_reply_id) if _reply_id is not None else None),
                root_job_id=root_job_id,
            )
        )
        await pool._observer.index_turn_async(
            str(_reply_id) if _reply_id is not None else None,
            session_id=pool.session_id,
            role="assistant",
        )

    result.metadata["_on_dispatched"] = TrustedCallback(_log_turn)


def is_pool_turn_error(exc: BaseException) -> bool:
    return isinstance(exc, _POOL_TURN_ERRORS) or is_provider_error(exc)
