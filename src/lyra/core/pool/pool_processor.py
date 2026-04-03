"""Processing engine extracted from Pool (issue #300).

Owns cancel-in-flight loop, guarded execution, timeout handling, and
dispatch logic.  Pool retains session state and public API.
"""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import importlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..message import InboundMessage
    from .pool import Pool

from ..message import GENERIC_ERROR_REPLY, OutboundMessage, Response
from ..render_events import RenderEvent, TextRenderEvent

log = logging.getLogger(__name__)

# Below this threshold, log a warning for possible dead backend or empty response.
_MIN_EXPECTED_DURATION_MS = 100


class PoolProcessor:
    """Async processing engine for a Pool.

    Encapsulates the debounce → cancel-in-flight → guarded-execute → dispatch
    pipeline.  Holds no session state — reads everything from the owning Pool.
    Internal to Pool — do not instantiate directly.
    """

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def process_loop(self) -> None:  # noqa: C901 — debounce + cancel-in-flight adds inherent branches
        """Consume inbox with debounce aggregation and cancel-in-flight."""
        pool = self._pool
        _last_msg: InboundMessage | None = None
        try:
            while True:
                if pool._inbox.empty():
                    break
                buffer = await pool._debouncer.collect(pool._inbox)

                agent = pool._ctx.get_agent(pool.agent_name)
                if agent is None:
                    log.error(
                        "no agent %r for pool %s — message(s) dropped",
                        pool.agent_name,
                        pool.pool_id,
                    )
                    # Drain any remaining queued messages before exiting
                    while not pool._inbox.empty():
                        try:
                            pool._inbox.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    break

                # Phase 2: process with cancel-in-flight
                msg = pool._debouncer.merge(buffer)
                _last_msg = msg
                _last_msg = await self._process_with_cancel(msg, buffer, agent)
        except asyncio.CancelledError:
            # /stop cancellation — send a reply if we have a message context.
            if _last_msg is not None:
                _reply = pool._msg("cancelled", "Request cancelled.")
                await asyncio.shield(
                    self._safe_dispatch(_last_msg, Response(content=_reply))
                )
            raise
        finally:
            pool._current_task = None

    async def _process_with_cancel(
        self,
        msg: InboundMessage,
        buffer: list[InboundMessage],
        agent: AgentBase,
    ) -> InboundMessage:
        """Run agent.process(), with optional cancel-in-flight on new messages.

        Default mode (cancel_on_new_message=False): awaits the agent to completion;
        any messages that arrive during processing queue naturally in pool._inbox and
        are picked up by the next process_loop() iteration.

        Cancel-in-flight mode (cancel_on_new_message=True): races the agent against
        the inbox; a new message aborts the ongoing turn, merges the context, and
        re-dispatches from scratch.
        """
        pool = self._pool

        while True:
            agent_task = asyncio.create_task(
                self._guarded_process_one(msg, agent),
                name=f"agent:{pool.pool_id}",
            )

            if not pool.cancel_on_new_message:
                # "Let it finish" mode — new messages accumulate in inbox and are
                # consumed naturally by the next process_loop() iteration.
                await agent_task
                if agent_task.cancelled():
                    raise asyncio.CancelledError
                return msg

            # ----------------------------------------------------------------
            # Cancel-in-flight mode: race agent against next inbox message.
            # ----------------------------------------------------------------
            inbox_waiter = asyncio.create_task(
                pool._inbox.get(), name=f"inbox:{pool.pool_id}"
            )

            done, _ = await asyncio.wait(
                {agent_task, inbox_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if agent_task in done:
                # Agent finished — errors handled inside _guarded_process_one.
                inbox_waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inbox_waiter
                # If inbox_waiter also completed (race), put the message back.
                if inbox_waiter in done and not inbox_waiter.cancelled():
                    try:
                        pool._inbox.put_nowait(inbox_waiter.result())
                    except (asyncio.QueueFull, Exception):
                        log.warning(
                            "pool %s: message lost in inbox race",
                            pool.pool_id,
                            exc_info=True,
                        )
                # Propagate CancelledError from /stop.
                if agent_task.cancelled():
                    raise asyncio.CancelledError
                return msg

            # New message arrived while agent was processing → cancel-in-flight.
            new_msg = inbox_waiter.result()
            # Mark any in-flight streaming as superseded BEFORE yielding to the
            # event loop.  asyncio is single-threaded: the flag is guaranteed to
            # be visible to the dispatcher worker the next time it runs (i.e.
            # after the `await agent_task` below).  This prevents the dispatcher
            # from sending an orphaned "…" placeholder for the cancelled turn.
            _inflight = pool._inflight_stream_outbound
            if _inflight is not None:
                _inflight.metadata["_superseded"] = True
                pool._inflight_stream_outbound = None
            agent_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await agent_task

            log.debug(
                "cancel-in-flight in pool %s: new message while LLM processing",
                pool.pool_id,
            )

            # Debounce the new message (drain any rapid follow-ups).
            buffer.append(new_msg)
            buffer.extend(await pool._debouncer.drain_followups(pool._inbox))

            msg = pool._debouncer.merge(buffer)

    async def _guarded_process_one(self, msg: InboundMessage, agent: AgentBase) -> None:
        """Wrap _process_one with timeout and error handling."""
        pool = self._pool
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
                    self._process_one(msg, agent), timeout=pool._turn_timeout
                )
            else:
                await self._process_one(msg, agent)
            _duration_ms = (time.monotonic() - _start) * 1000
            log.info(
                "agent completed: agent=%s pool=%s duration_ms=%.0f",
                pool.agent_name,
                pool.pool_id,
                _duration_ms,
            )
            _had_error = pool._last_turn_had_backend_error
            pool._last_turn_had_backend_error = False
            if _duration_ms < _MIN_EXPECTED_DURATION_MS or _had_error:
                log.warning(
                    "agent suspiciously fast: agent=%s pool=%s"
                    " duration_ms=%.0f backend_error=%s"
                    " — possible dead backend",
                    pool.agent_name,
                    pool.pool_id,
                    _duration_ms,
                    _had_error,
                )
                pool._ctx.record_dead_backend_hit()
            else:
                # Backend responded normally — clear any prior dead-backend hits
                # so the health check stops alerting once the backend recovers.
                pool._ctx.reset_dead_backend_hits()
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
            await self._safe_dispatch(msg, Response(content=_reply))
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
            await self._safe_dispatch(msg, Response(content=_reply))
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

    async def _process_one(self, msg: InboundMessage, agent: AgentBase) -> None:  # noqa: C901, PLR0915 — session-id update adds branches
        """Run agent.process and dispatch result (streaming or non-streaming)."""
        pool = self._pool
        await pool.append(msg)

        # Inject voice modality — must happen before _original_msg is
        # captured so dispatch_streaming sees it.  Covers:
        #   - pool.voice_mode toggle (/voice → /text)
        #   - /voice <prompt> one-shot (agent rewrites text internally,
        #     but _original_msg needs modality set here)
        #   - voice messages (modality already "voice" from audio pipeline)
        if msg.modality != "voice" and (
            pool.voice_mode
            or (msg.text and msg.text.strip().lower().startswith("/voice "))
        ):
            import dataclasses

            msg = dataclasses.replace(msg, modality="voice")

        _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
        if _ensure_fn is not None:
            await _ensure_fn(pool)  # S3 — cache system prompt

        async def _intermediate_cb(turn_text: str) -> None:
            await self._safe_dispatch(
                msg,
                Response(content=f"⏳ {turn_text}", intermediate=True),
            )

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
                from lyra.core.processor_registry import registry as _proc_registry

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
                            f"⚠️ Command {_cmd_name} failed to prepare."
                            " Please try again.",
                        )
                        await self._safe_dispatch(msg, Response(content=_error_reply))
                        return

        result = agent.process(msg, pool, on_intermediate=_intermediate_cb)
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
            # Fix #373: tee the iterator to capture streamed content for turn logging.
            # Save original ref before wrapping — session_id lives on the original.
            _result_iter_for_sid = result
            _content_parts: list[str] = []
            _cfg = getattr(agent, "config", None)
            _emit_tool_recap = _cfg.show_tool_recap if _cfg is not None else True
            # Signal when the stream is fully consumed so post() can run (#372).
            _stream_done = asyncio.Event() if _processor is not None else None

            async def _capture() -> collections.abc.AsyncGenerator[RenderEvent, None]:
                # S4: _result_iter_for_sid yields RenderEvent from StreamProcessor.
                # Collect TextRenderEvent.text for turn logging; forward all events.
                # When show_tool_recap is False, ToolSummaryRenderEvent is filtered out.
                try:
                    async for event in _result_iter_for_sid:
                        if isinstance(event, TextRenderEvent):
                            _content_parts.append(event.text)
                            if event.is_error:
                                pool._last_turn_had_backend_error = True
                        elif not _emit_tool_recap:
                            # ToolSummaryRenderEvent — suppress when recap is disabled
                            continue
                        yield event
                finally:
                    _aclose = getattr(_result_iter_for_sid, "aclose", None)
                    if callable(_aclose):
                        await _aclose()  # type: ignore[misc]
                    if _stream_done is not None:
                        _stream_done.set()

            result = _capture()  # type: ignore[assignment]  # AsyncGenerator is a subtype of AsyncIterator

            # Bug 1 (#316): pass an OutboundMessage so the adapter can write
            # reply_message_id on it; attach a callback for deferred turn logging.
            _outbound = OutboundMessage.from_text("")
            # Register before dispatch so _process_with_cancel can supersede it.
            pool._inflight_stream_outbound = _outbound

            async def _log_streaming_turn(outbound: OutboundMessage) -> None:
                # Clear inflight reference once streaming is fully delivered.
                if pool._inflight_stream_outbound is outbound:
                    pool._inflight_stream_outbound = None
                # Propagate CLI session_id from the (now-consumed) iterator.
                # When an OutboundDispatcher is used, dispatch_streaming returns
                # immediately (fire-and-forget); the iterator is consumed later
                # in the worker task.  Reading session_id here (in the
                # _on_dispatched callback) guarantees the iterator has finished.
                _stream_sid = getattr(_result_iter_for_sid, "session_id", None)
                if _stream_sid and pool.session_id != _stream_sid:
                    await pool._observer.end_session_async(pool.session_id)
                    pool.session_id = _stream_sid
                await pool._observer.session_update_async(_original_msg)
                _reply_id = outbound.metadata.get("reply_message_id")
                await pool._observer.log_turn_async(
                    role="assistant",
                    platform=_platform,
                    user_id=_user_id,
                    content="".join(_content_parts),
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

            _outbound.metadata["_on_dispatched"] = _log_streaming_turn
            try:
                await pool._ctx.dispatch_streaming(_original_msg, result, _outbound)
                pool._ctx.record_circuit_success()
            except BaseException as exc:
                pool._ctx.record_circuit_failure(exc)
                raise
            # Fallback: update session_id here for the no-dispatcher path
            # (where dispatch_streaming blocks until streaming finishes).
            # The _on_dispatched callback above handles the dispatcher path.
            _stream_sid = getattr(_result_iter_for_sid, "session_id", None)
            if _stream_sid and pool.session_id != _stream_sid:
                await pool._observer.end_session_async(pool.session_id)
                pool.session_id = _stream_sid

            # Processor post-hook for streaming agents (#372).
            # Wait for stream to be fully consumed, then call post() with the
            # captured content.  post() runs fire-and-forget for side effects
            # (e.g. vault save) — the user response is already streamed.
            if _processor is not None and _stream_done is not None:
                await _stream_done.wait()
                _streamed = Response(content="".join(_content_parts))
                try:
                    await _processor.post(_original_msg, _streamed)
                except Exception:
                    log.warning("Processor post() failed (streaming)", exc_info=True)
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

                result.metadata["_on_dispatched"] = _log_turn
            await pool._ctx.dispatch_response(_original_msg, result)
            await pool._observer.session_update_async(_original_msg)

        _compact_fn = getattr(agent, "compact", None)
        if _compact_fn is not None:
            await _compact_fn(pool)  # S5 — check compaction threshold after each turn

    async def _safe_dispatch(self, msg: InboundMessage, response: Response) -> None:
        pool = self._pool
        try:
            await asyncio.wait_for(
                pool._ctx.dispatch_response(msg, response),
                timeout=pool._safe_dispatch_timeout,
            )
        except Exception as exc:
            log.exception("_safe_dispatch failed for pool %s: %s", pool.pool_id, exc)
