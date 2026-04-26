"""Execution functions extracted from PoolProcessor (issue #753).

Contains the guarded execution and single-message processing logic.
"""

from __future__ import annotations

import asyncio
import collections.abc
import importlib
import logging
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage
    from .pool import Pool

from collections.abc import Callable

from ..messaging.callbacks import TrustedCallback
from ..messaging.message import GENERIC_ERROR_REPLY, OutboundMessage, Response
from .pool_processor_streaming import (
    build_streaming_capture,
    build_streaming_turn_logger,
    run_streaming_turn_post,
)

log = logging.getLogger(__name__)


async def guarded_process_one(
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> None:
    """Wrap process_one with timeout and error handling."""
    _start = time.monotonic()
    _cancelled = False
    log.info(
        "agent started: agent=%s pool=%s scope=%s",
        pool.agent_name,
        pool.pool_id,
        msg.scope_id,
    )
    try:
        if pool._turn_timeout is not None:
            await asyncio.wait_for(
                process_one(msg, agent, pool), timeout=pool._turn_timeout
            )
        else:
            await process_one(msg, agent, pool)
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


async def process_one(  # noqa: C901, PLR0915 — session-id update adds branches
    msg: InboundMessage, agent: AgentBase, pool: Pool
) -> None:
    """Run agent.process and dispatch result (streaming or non-streaming)."""
    await pool.append(msg)

    # Inject voice modality — must happen before _original_msg is
    # captured so dispatch_streaming sees it.  Covers:
    #   - pool.voice_mode toggle (/voice → /text)
    #   - /voice <prompt> one-shot (agent rewrites text internally,
    #     but _original_msg needs modality set here)
    #   - voice messages (modality already "voice" from audio pipeline)
    if msg.modality != "voice" and (
        pool.voice_mode or (msg.text and msg.text.strip().lower().startswith("/voice "))
    ):
        import dataclasses

        msg = dataclasses.replace(msg, modality="voice")

    _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
    if _ensure_fn is not None:
        await _ensure_fn(pool)  # S3 — cache system prompt

    # Processor pre-hook: enrich message before LLM (B1 — issue #363).
    # pool.append gets the original so history shows the real command;
    # agent.process gets the enriched version so the LLM sees scraped content.
    _processor = None
    _original_msg = msg
    if msg.command is not None:
        _session_tools = getattr(agent, "_session_tools", None)
        if _session_tools is not None:
            # Import inside the function to avoid circular imports:
            # processors → processor_registry → message;
            # pool_processor also imports message.
            # Python caches modules in sys.modules after the first import,
            # so this is effectively free (one dict lookup) on every call.
            # registers processors via @register decorators
            importlib.import_module("lyra.core.processors")
            from lyra.core.processors.processor_registry import (
                registry as _proc_registry,
            )

            _cmd_name = f"{msg.command.prefix}{msg.command.name}"
            _processor = _proc_registry.build(_cmd_name, _session_tools)
            if _processor is not None:
                try:
                    msg = await _processor.pre(msg)
                except Exception:
                    log.warning(
                        "Processor pre() failed for %s", _cmd_name, exc_info=True
                    )
                    # Surface the error rather than falling through to the LLM
                    # with an unmodified message (confusing non-response).
                    _error_reply = pool._msg(
                        "generic",
                        f"Command {_cmd_name} failed to prepare. Please try again.",
                    )
                    await _safe_dispatch(msg, Response(content=_error_reply), pool)
                    return

    result = agent.process(msg, pool)
    if not isinstance(result, collections.abc.AsyncIterator):  # pyright: ignore[reportUnnecessaryIsInstance]
        # Regular coroutine — await to get the actual result
        try:
            result = await result  # type: ignore[misc]  # coroutine → Response|AsyncIterator
        except Exception as exc:
            pool._ctx.record_circuit_failure(exc)
            raise
        # Processor post-hook: side effects after LLM response (B1 — issue #363).
        if _processor is not None and isinstance(result, Response):
            try:
                result = await _processor.post(_original_msg, result)
            except Exception:
                log.warning("Processor post() failed", exc_info=True)

    # Capture values for the deferred turn-logging callback (#316).
    _platform = pool.medium or str(msg.platform)
    _user_id = pool.user_id or msg.user_id

    if isinstance(result, collections.abc.AsyncIterator):
        # Streaming path — delegate to helpers in pool_processor_streaming.py
        _result_iter_for_sid = result
        _content_parts: list[str] = []
        _cfg = getattr(agent, "config", None)
        _emit_tool_recap = _cfg.show_tool_recap if _cfg is not None else True
        _stream_done = asyncio.Event() if _processor is not None else None

        # Wrap iterator to capture text content and signal completion
        result = build_streaming_capture(
            _result_iter_for_sid,
            _content_parts,
            pool,
            _stream_done,
            _emit_tool_recap,
        )

        # Build outbound with turn-logging callback
        _outbound, _log_callback = build_streaming_turn_logger(
            pool,
            _result_iter_for_sid,
            _original_msg,
            _platform,
            _user_id,
            _content_parts,
        )
        pool._inflight_stream_outbound = _outbound
        _outbound.metadata["_on_dispatched"] = TrustedCallback(
            cast("Callable[..., object]", _log_callback)
        )

        try:
            await pool._ctx.dispatch_streaming(_original_msg, result, _outbound)
            pool._ctx.record_circuit_success()
        except BaseException as exc:
            pool._ctx.record_circuit_failure(exc)
            raise

        # Fallback session_id update for no-dispatcher path
        _stream_sid = getattr(_result_iter_for_sid, "session_id", None)
        if _stream_sid and pool.session_id != _stream_sid:
            await pool._observer.end_session_async(pool.session_id)
            pool.session_id = _stream_sid

        # Processor post-hook for streaming agents (#372)
        await run_streaming_turn_post(
            _processor, _stream_done, _original_msg, _content_parts
        )
    else:
        pool._ctx.record_circuit_success()
        # Update session_id with the real Claude CLI session UUID (#316).
        if isinstance(result, Response):  # pyright: ignore[reportUnnecessaryIsInstance]
            _cli_session_id = result.metadata.get("session_id")
            if _cli_session_id:
                if pool.session_id != _cli_session_id:
                    await pool._observer.end_session_async(pool.session_id)
                pool.session_id = _cli_session_id
        # Attach deferred turn-logging callback after adapter sends (#316).
        if isinstance(result, Response):  # pyright: ignore[reportUnnecessaryIsInstance]
            _content = result.content

            async def _log_turn(outbound: OutboundMessage) -> None:
                _reply_id = outbound.metadata.get("reply_message_id")
                await pool._observer.log_turn_async(
                    role="assistant",
                    platform=_platform,
                    user_id=_user_id,
                    content=_content,
                    reply_message_id=(
                        str(_reply_id) if _reply_id is not None else None
                    ),
                )
                # Index assistant turn for reply-to session routing (#341).
                await pool._observer.index_turn_async(
                    str(_reply_id) if _reply_id is not None else None,
                    session_id=pool.session_id,
                    role="assistant",
                )

            result.metadata["_on_dispatched"] = TrustedCallback(_log_turn)
        await pool._ctx.dispatch_response(_original_msg, result)
        await pool._observer.session_update_async(_original_msg)

    _compact_fn = getattr(agent, "compact", None)
    if _compact_fn is not None:
        await _compact_fn(pool)  # S5 — check compaction threshold after each turn


async def _safe_dispatch(msg: InboundMessage, response: Response, pool: Pool) -> None:
    """Dispatch with timeout and error handling."""
    try:
        await asyncio.wait_for(
            pool._ctx.dispatch_response(msg, response),
            timeout=pool._safe_dispatch_timeout,
        )
    except Exception as exc:
        log.exception("_safe_dispatch failed for pool %s: %s", pool.pool_id, exc)
