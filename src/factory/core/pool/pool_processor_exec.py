"""Execution functions extracted from PoolProcessor (issue #753).

Contains the guarded execution and single-message processing logic.
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage
    from .pool import Pool

from factory.transport.typing_publisher import is_typing_enabled
from factory.transport.work_scope import WorkScope

from ..messaging.message import GENERIC_ERROR_REPLY, Response
from ..trace import TraceContext
from .pool_active_jobs import _close_active_job, _open_active_job
from .pool_processor_dispatch import (
    is_pool_turn_error,
    process_non_streaming,
    process_streaming,
    resolve_non_streaming,
    run_processor_pre,
    safe_dispatch,
)
from .pool_trace_context import pool_turn_trace_context

log = logging.getLogger(__name__)


async def guarded_process_one(  # noqa: PLR0915, C901 — DEBT:complexity-residual
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> None:
    """Wrap process_one with timeout and error handling.

    Per-turn active-jobs open/close uses TraceContext ``root_job_id`` so the
    registry id equals the wire envelope job_id drivers mint (#2147).
    """
    with pool_turn_trace_context(
        msg, pool_id=pool.pool_id, agent_name=pool.agent_name
    ) as trace_id:
        # Same id drivers will stamp via mint_work_envelope_fields.
        wire_job_id = TraceContext.get_root_job_id()
        reg_job_id = await _open_active_job(pool, job_id=wire_job_id)
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
            await safe_dispatch(msg, Response(content=_reply), pool)
            log.warning(
                "agent failed: agent=%s pool=%s error=timeout",
                pool.agent_name,
                pool.pool_id,
            )
        except asyncio.CancelledError:
            _cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: pool-turn — ProviderError duck-type via is_pool_turn_error
            if not is_pool_turn_error(exc):
                raise
            log.exception("unhandled error in pool %s: %s", pool.pool_id, exc)
            _reply = pool._msg("generic", GENERIC_ERROR_REPLY)
            await safe_dispatch(msg, Response(content=_reply), pool)
            pool._ctx.record_circuit_failure(exc)
            log.warning(
                "agent failed: agent=%s pool=%s error=%s",
                pool.agent_name,
                pool.pool_id,
                str(exc)[:200],
            )
        finally:
            await _close_active_job(pool, reg_job_id)
            if not _cancelled:
                log.info(
                    "agent idle: agent=%s pool=%s",
                    pool.agent_name,
                    pool.pool_id,
                )


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
    _enriched_msg, _processor = await run_processor_pre(msg, agent, pool)
    if _enriched_msg is None:
        return
    msg = _enriched_msg
    result = agent.process(msg, pool)
    if not isinstance(result, collections.abc.AsyncIterator):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        result = await resolve_non_streaming(result, _processor, _original_msg, pool)
    _platform = pool.medium or str(msg.platform)
    _user_id = pool.user_id or msg.user_id
    if isinstance(result, collections.abc.AsyncIterator):
        await process_streaming(
            result, _processor, _original_msg, _platform, _user_id, pool
        )
    else:
        await process_non_streaming(result, _original_msg, _platform, _user_id, pool)
    _compact_fn = getattr(agent, "compact", None)
    if _compact_fn is not None:
        await _compact_fn(pool)
