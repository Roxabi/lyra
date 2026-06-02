"""Streaming helpers extracted from pool_processor_exec (issue #753).

Contains the async generator capture wrapper, streaming turn-logging callback,
and streaming/non-streaming dispatch helpers (#1636).
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..messaging.message import InboundMessage
    from .pool import Pool

from ..messaging.message import OutboundMessage, Response
from ..messaging.render_events import RenderEvent, TextDeltaRenderEvent
from ..messaging.utils.callbacks import TrustedCallback
from .pool_observer import TurnLogDeps

log = logging.getLogger(__name__)


def build_streaming_capture(
    result_iter: collections.abc.AsyncIterator[RenderEvent],
    content_parts: list[str],
    pool: Pool,
    stream_done_event: object | None,
) -> collections.abc.AsyncGenerator[RenderEvent, None]:
    """Build an async generator that captures TextDeltaRenderEvent content.

    Wraps the result iterator to collect text content for turn logging while
    forwarding all events. v1 TextRenderEvent / ToolSummaryRenderEvent removed
    in Slice 5 (#1192); content is now accumulated from TextDeltaRenderEvent deltas.
    """

    async def _capture() -> collections.abc.AsyncGenerator[RenderEvent, None]:
        try:
            async for event in result_iter:
                if isinstance(event, TextDeltaRenderEvent):
                    content_parts.append(event.delta)
                yield event
        finally:
            _aclose = getattr(result_iter, "aclose", None)
            if callable(_aclose):
                await _aclose()  # type: ignore[misc] — DEBT:defensive-narrow-payloads
            if stream_done_event is not None:
                stream_done_event.set()  # type: ignore[misc] — DEBT:defensive-narrow-payloads

    return _capture()


@dataclass(frozen=True)
class StreamLogDeps:
    """Dependencies for build_streaming_turn_logger."""

    pool: Pool
    result_iter_for_sid: collections.abc.AsyncIterator[RenderEvent]
    original_msg: InboundMessage
    platform: str
    user_id: str
    content_parts: list[str]


def build_streaming_turn_logger(
    deps: StreamLogDeps,
) -> tuple[OutboundMessage, Callable[[OutboundMessage], Awaitable[None]]]:
    """Build OutboundMessage with turn-logging callback for streaming responses.

    Returns:
        Tuple of (outbound_msg, log_callback) where callback should be attached
        to outbound.metadata["_on_dispatched"].
    """
    outbound = OutboundMessage.from_text("")

    async def _log_streaming_turn(outbound_msg: OutboundMessage) -> None:
        # Clear inflight reference once streaming is fully delivered.
        if deps.pool._inflight_stream_outbound is outbound_msg:
            deps.pool._inflight_stream_outbound = None
        # Propagate CLI session_id from the (now-consumed) iterator.
        _stream_sid = getattr(deps.result_iter_for_sid, "session_id", None)
        if _stream_sid and deps.pool.session_id != _stream_sid:
            await deps.pool._observer.end_session_async(deps.pool.session_id)
            deps.pool.session_id = _stream_sid
        await deps.pool._observer.session_update_async(deps.original_msg)
        _reply_id = outbound_msg.metadata.get("reply_message_id")
        await deps.pool._observer.log_turn_async(
            TurnLogDeps(
                role="assistant",
                platform=deps.platform,
                user_id=deps.user_id,
                content="".join(deps.content_parts),
                reply_message_id=(str(_reply_id) if _reply_id is not None else None),
            )
        )
        # Index assistant turn for reply-to session routing (#341).
        await deps.pool._observer.index_turn_async(
            str(_reply_id) if _reply_id is not None else None,
            session_id=deps.pool.session_id,
            role="assistant",
        )

    return outbound, _log_streaming_turn


async def run_streaming_turn_post(
    processor: object | None,
    stream_done_event: object | None,
    original_msg: InboundMessage,
    content_parts: list[str],
) -> None:
    """Run processor post-hook after streaming is fully consumed (#372)."""
    if processor is None or stream_done_event is None:
        return
    await stream_done_event.wait()  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    streamed = Response(content="".join(content_parts))
    try:
        # processor.post is a coroutine
        await asyncio.create_task(processor.post(original_msg, streamed))  # type: ignore[misc] — DEBT:defensive-narrow-payloads
    except Exception:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
        log.warning("Processor post() failed (streaming)", exc_info=True)


async def _update_session_id(result: object, pool: Pool) -> None:
    """Update pool session_id from a non-streaming Response (#316)."""
    if not isinstance(result, Response):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        return
    _cli_session_id = result.metadata.get("session_id")
    if _cli_session_id:
        if pool.session_id != _cli_session_id:
            await pool._observer.end_session_async(pool.session_id)
        pool.session_id = _cli_session_id


def _capture_turn_log(result: object, pool: Pool, platform: str, user_id: str) -> None:
    """Attach deferred turn-logging callback to result after adapter sends (#316)."""
    if not isinstance(result, Response):  # pyright: ignore[reportUnnecessaryIsInstance] — DEBT:defensive-narrow-payloads
        return
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
            )
        )
        # Index assistant turn for reply-to session routing (#341).
        await pool._observer.index_turn_async(
            str(_reply_id) if _reply_id is not None else None,
            session_id=pool.session_id,
            role="assistant",
        )

    result.metadata["_on_dispatched"] = TrustedCallback(_log_turn)


@dataclass(frozen=True)
class DispatchDeps:
    """Shared dispatch context for streaming and non-streaming paths (#1636)."""

    pool: Pool
    original_msg: InboundMessage
    platform: str
    user_id: str


async def dispatch_non_streaming(result: object, deps: DispatchDeps) -> None:
    """Dispatch a non-streaming Response and update session + turn log (#316)."""
    deps.pool._ctx.record_circuit_success()
    await _update_session_id(result, deps.pool)
    _capture_turn_log(result, deps.pool, deps.platform, deps.user_id)
    await deps.pool._ctx.dispatch_response(deps.original_msg, result)  # type: ignore[arg-type] — DEBT:defensive-narrow-payloads
    await deps.pool._observer.session_update_async(deps.original_msg)


async def dispatch_streaming(
    result: collections.abc.AsyncIterator,  # type: ignore[type-arg]
    processor: object | None,
    deps: DispatchDeps,
) -> None:
    """Dispatch a streaming AsyncIterator result with capture + turn logging."""
    _result_iter_for_sid = result
    _content_parts: list[str] = []
    _stream_done = asyncio.Event() if processor is not None else None

    # Wrap iterator to capture text content and signal completion
    result = build_streaming_capture(result, _content_parts, deps.pool, _stream_done)

    # Build outbound with turn-logging callback
    _outbound, _log_callback = build_streaming_turn_logger(
        StreamLogDeps(
            pool=deps.pool,
            result_iter_for_sid=_result_iter_for_sid,
            original_msg=deps.original_msg,
            platform=deps.platform,
            user_id=deps.user_id,
            content_parts=_content_parts,
        )
    )
    deps.pool._inflight_stream_outbound = _outbound
    _outbound.metadata["_on_dispatched"] = TrustedCallback(_log_callback)

    try:
        await deps.pool._ctx.dispatch_streaming(deps.original_msg, result, _outbound)
        deps.pool._ctx.record_circuit_success()
    except BaseException as exc:
        deps.pool._ctx.record_circuit_failure(exc)
        raise

    # Fallback session_id update for no-dispatcher path
    _stream_sid = getattr(_result_iter_for_sid, "session_id", None)
    if _stream_sid and deps.pool.session_id != _stream_sid:
        await deps.pool._observer.end_session_async(deps.pool.session_id)
        deps.pool.session_id = _stream_sid

    # Processor post-hook for streaming agents (#372)
    await run_streaming_turn_post(
        processor, _stream_done, deps.original_msg, _content_parts
    )
