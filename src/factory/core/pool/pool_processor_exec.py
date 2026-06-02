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

from factory.transport.typing_publisher import is_typing_enabled
from factory.transport.work_scope import WorkScope

from ..messaging.message import GENERIC_ERROR_REPLY, Response
from ..trace import TraceContext
from .pool_processor_streaming import (
    DispatchDeps,
    dispatch_non_streaming,
    dispatch_streaming,
)

log = logging.getLogger(__name__)


async def guarded_process_one(  # noqa: PLR0915 — DEBT:complexity-residual
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
        except Exception as exc:
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
    msg = _inject_voice_modality(msg, pool)

    _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
    if _ensure_fn is not None:
        await _ensure_fn(pool)  # S3 — cache system prompt

    _processor, _original_msg, msg, _early_return = await _run_processor_pre(
        msg, agent, pool
    )
    if _early_return:
        return

    result = await _invoke_agent(agent, msg, pool, _processor, _original_msg)

    _deps = DispatchDeps(
        pool=pool,
        original_msg=_original_msg,
        platform=pool.medium or str(msg.platform),
        user_id=pool.user_id or msg.user_id,
    )

    if isinstance(result, collections.abc.AsyncIterator):
        await dispatch_streaming(result, _processor, _deps)
    else:
        await dispatch_non_streaming(result, _deps)

    _compact_fn = getattr(agent, "compact", None)
    if _compact_fn is not None:
        await _compact_fn(pool)  # S5 — check compaction threshold after each turn


def _inject_voice_modality(msg: InboundMessage, pool: Pool) -> InboundMessage:
    """Inject voice modality when pool or command requires it.

    Must happen before _original_msg is captured so dispatch_streaming sees it.
    Covers pool.voice_mode toggle, /voice <prompt> one-shot, and audio messages.
    """
    if msg.modality != "voice" and (
        pool.voice_mode or (msg.text and msg.text.strip().lower().startswith("/voice "))
    ):
        import dataclasses

        return dataclasses.replace(msg, modality="voice")
    return msg


async def _invoke_agent(
    agent: AgentBase,
    msg: InboundMessage,
    pool: Pool,
    processor: object | None,
    original_msg: InboundMessage,
) -> object:
    """Call agent.process, await coroutine if needed, run processor post-hook."""
    result = agent.process(msg, pool)
    if isinstance(result, collections.abc.AsyncIterator):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        return result
    # Regular coroutine — await to get the actual result
    try:
        result = await result  # type: ignore[misc] — DEBT:defensive-narrow-payloads  # coroutine → Response|AsyncIterator
    except Exception as exc:
        pool._ctx.record_circuit_failure(exc)
        raise
    # Processor post-hook: side effects after LLM response (B1 — issue #363).
    if processor is not None and isinstance(result, Response):
        try:
            result = await processor.post(original_msg, result)  # type: ignore[misc] — DEBT:defensive-narrow-payloads
        except Exception:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
            log.warning("Processor post() failed", exc_info=True)
    return result


async def _run_processor_pre(
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> tuple[object | None, InboundMessage, InboundMessage, bool]:
    """Run processor pre-hook if applicable (B1 — issue #363).

    Returns (processor, original_msg, enriched_msg, early_return).
    early_return=True means pre-hook failed; error dispatched; caller must return.
    """
    _processor = None
    _original_msg = msg
    if msg.command is None:
        return None, _original_msg, msg, False
    _session_tools = getattr(agent, "_session_tools", None)
    if _session_tools is None:
        return None, _original_msg, msg, False
    # Lazy import — avoids circular dependency (processors→registry→message);
    # sys.modules caches it after first call (effectively free on repeat calls).
    importlib.import_module("factory.core.processors")  # registers via @register
    from factory.core.processors.processor_registry import registry as _proc_registry

    _cmd_name = f"{msg.command.prefix}{msg.command.name}"
    _processor = _proc_registry.build(_cmd_name, _session_tools)
    if _processor is None:
        return None, _original_msg, msg, False
    try:
        msg = await _processor.pre(msg)
    except Exception:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
        log.warning("Processor pre() failed for %s", _cmd_name, exc_info=True)
        # Surface the error rather than falling through to the LLM
        # with an unmodified message (confusing non-response).
        _error_reply = pool._msg(
            "generic", f"Command {_cmd_name} failed to prepare. Please try again."
        )
        await _safe_dispatch(msg, Response(content=_error_reply), pool)
        return None, _original_msg, msg, True
    return _processor, _original_msg, msg, False


async def _safe_dispatch(msg: InboundMessage, response: Response, pool: Pool) -> None:
    """Dispatch with timeout and error handling."""
    try:
        await asyncio.wait_for(
            pool._ctx.dispatch_response(msg, response),
            timeout=pool._safe_dispatch_timeout,
        )
    except Exception as exc:
        log.exception("_safe_dispatch failed for pool %s: %s", pool.pool_id, exc)
