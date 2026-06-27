"""Execution functions extracted from PoolProcessor (issue #753).

Contains the guarded execution and single-message processing logic.
"""

from __future__ import annotations

import asyncio
import collections.abc
import importlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage
    from .pool import Pool

from uuid import uuid4

from factory.core.provider_match import is_provider_error
from factory.transport.typing_publisher import is_typing_enabled
from factory.transport.work_scope import WorkScope

from ..messaging.message import GENERIC_ERROR_REPLY, OutboundMessage, Response
from ..messaging.utils.callbacks import TrustedCallback
from ..trace import TraceContext
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


def _is_pool_turn_error(exc: BaseException) -> bool:
    return isinstance(exc, _POOL_TURN_ERRORS) or is_provider_error(exc)
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


async def guarded_process_one(  # noqa: PLR0915, C901 — DEBT:complexity-residual
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> None:
    """Wrap process_one with timeout and error handling."""
    token_an = TraceContext.set_agent_name(pool.agent_name)
    try:
        _start = time.monotonic()
        _cancelled = False
        log.info(
            "agent started: agent=%s pool=%s scope=%s",
            pool.agent_name,
            pool.pool_id,
            msg.scope_id,
        )
        try:
            try:
                scope_id = int(msg.scope_id.rsplit(":", 1)[-1])
            except ValueError:
                log.warning("malformed scope_id %r", msg.scope_id)
                scope_id = 0
            trace_id = TraceContext.get_trace_id() or uuid4().hex
            work_scope = WorkScope(
                platform=msg.platform,
                bot_id=msg.bot_id,
                scope_id=scope_id,
                trace_id=trace_id,
            )

            async def _run_process_one() -> None:
                if pool._turn_timeout is not None:
                    await asyncio.wait_for(
                        process_one(msg, agent, pool), timeout=pool._turn_timeout
                    )
                else:
                    await process_one(msg, agent, pool)

            if is_typing_enabled() and pool.typing_publisher is not None:
                async with pool.typing_publisher.scope(work_scope):
                    await _run_process_one()
            else:
                await _run_process_one()
            _duration_ms = (time.monotonic() - _start) * 1000
            log.info(
                "agent completed: agent=%s pool=%s duration_ms=%.0f",
                pool.agent_name,
                pool.pool_id,
                _duration_ms,
            )
        except asyncio.TimeoutError:
            log.warning(
                "pool %s: turn timeout after %.0fs — killing backend",
                pool.pool_id,
                pool._turn_timeout,
            )
            if not agent.is_backend_alive(pool.pool_id):
                log.error(
                    "pool %s: backend process died — timeout caused by dead process",
                    pool.pool_id,
                )
            await agent.reset_backend(pool.pool_id)
            _reply = pool._msg("timeout", "Your request timed out. Please try again.")
            await _safe_dispatch(msg, Response(content=_reply), pool)
            log.warning(
                "agent failed: agent=%s pool=%s error=timeout",
                pool.agent_name,
                pool.pool_id,
            )
        except asyncio.CancelledError:
            _cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: pool-turn — ProviderError duck-type via is_provider_error
            if not _is_pool_turn_error(exc):
                raise
            log.exception("unhandled error in pool %s: %s", pool.pool_id, exc)
            _reply = pool._msg("generic", GENERIC_ERROR_REPLY)
            await _safe_dispatch(msg, Response(content=_reply), pool)
            pool._ctx.record_circuit_failure(exc)
            log.warning(
                "agent failed: agent=%s pool=%s error=%s",
                pool.agent_name,
                pool.pool_id,
                str(exc)[:200],
            )
        finally:
            if not _cancelled:
                log.info(
                    "agent idle: agent=%s pool=%s",
                    pool.agent_name,
                    pool.pool_id,
                )
    finally:
        TraceContext.reset_agent_name(token_an)


async def process_one(msg: InboundMessage, agent: AgentBase, pool: Pool) -> None:
    """Run agent.process and dispatch result (streaming or non-streaming)."""
    await pool.append(msg)
    if msg.modality != "voice" and (
        pool.voice_mode or (msg.text and msg.text.strip().lower().startswith("/voice "))
    ):
        import dataclasses

        msg = dataclasses.replace(msg, modality="voice")
    _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
    if _ensure_fn is not None:
        await _ensure_fn(pool)
    _original_msg = msg
    _enriched_msg, _processor = await _run_processor_pre(msg, agent, pool)
    if _enriched_msg is None:
        return
    msg = _enriched_msg
    result = agent.process(msg, pool)
    if not isinstance(result, collections.abc.AsyncIterator):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        result = await _resolve_non_streaming(result, _processor, _original_msg, pool)
    _platform = pool.medium or str(msg.platform)
    _user_id = pool.user_id or msg.user_id
    if isinstance(result, collections.abc.AsyncIterator):
        await _process_streaming(
            result, _processor, _original_msg, _platform, _user_id, pool
        )
    else:
        await _process_non_streaming(result, _original_msg, _platform, _user_id, pool)
    _compact_fn = getattr(agent, "compact", None)
    if _compact_fn is not None:
        await _compact_fn(pool)


async def _run_processor_pre(
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> tuple[InboundMessage | None, object]:
    """Run processor pre-hook (B1 — #363).

    Returns (enriched_msg, processor) on success, (None, None) if error dispatched.
    """
    if msg.command is None:
        return msg, None
    _session_tools = getattr(agent, "_session_tools", None)
    if _session_tools is None:
        return msg, None
    # Import inside fn to avoid circular imports (processors → registry → message).
    # sys.modules cache makes this free after first import.
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
        await _safe_dispatch(msg, Response(content=_error_reply), pool)
        return None, None
    return msg, _processor


async def _resolve_non_streaming(
    result: object, _processor: object | None, _original_msg: InboundMessage, pool: Pool
) -> object:
    """Await coroutine result and run processor post-hook if applicable."""
    try:
        result = await result  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    except _POOL_TURN_ERRORS as exc:
        pool._ctx.record_circuit_failure(exc)
        raise
    if _processor is not None and isinstance(result, Response):
        try:
            result = await _processor.post(_original_msg, result)  # type: ignore[union-attr] — DEBT:defensive-narrow-payloads
        except _PROCESSOR_HOOK_ERRORS:
            log.warning("Processor post() failed", exc_info=True)
    return result


async def _update_session_id(result: Response, pool: Pool) -> None:
    """Update pool session_id from CLI session UUID in response metadata (#316)."""
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
    """Attach deferred turn-logging callback to response metadata (#316)."""
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


async def _process_non_streaming(
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


async def _process_streaming(  # noqa: PLR0913 — 6 cohesive params, no natural grouping
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


async def _safe_dispatch(msg: InboundMessage, response: Response, pool: Pool) -> None:
    """Dispatch with timeout and error handling."""
    try:
        await asyncio.wait_for(
            pool._ctx.dispatch_response(msg, response),
            timeout=pool._safe_dispatch_timeout,
        )
    except _DISPATCH_ERRORS as exc:
        log.exception("_safe_dispatch failed for pool %s: %s", pool.pool_id, exc)
