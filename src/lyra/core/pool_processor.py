"""Processing engine extracted from Pool (issue #300).

Owns the cancel-in-flight loop, guarded execution, timeout handling,
and dispatch logic.  Pool retains session state and public API.
"""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import AgentBase
    from .message import InboundMessage
    from .pool import Pool

from .debouncer import MessageDebouncer
from .message import GENERIC_ERROR_REPLY, Response

log = logging.getLogger(__name__)

SAFE_DISPATCH_TIMEOUT = 10.0


class PoolProcessor:
    """Async processing engine for a Pool.

    Encapsulates the debounce → cancel-in-flight → guarded-execute → dispatch
    pipeline.  Holds no session state — reads everything from the owning Pool.

    Internal to Pool — do not instantiate directly.
    """

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

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
                msg = MessageDebouncer.merge(buffer)
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

    # ------------------------------------------------------------------
    # Cancel-in-flight
    # ------------------------------------------------------------------

    async def _process_with_cancel(
        self,
        msg: InboundMessage,
        buffer: list[InboundMessage],
        agent: AgentBase,
    ) -> InboundMessage:
        """Run agent.process(), cancelling and re-dispatching if new messages arrive."""
        pool = self._pool

        while True:
            agent_task = asyncio.create_task(
                self._guarded_process_one(msg, agent),
                name=f"agent:{pool.pool_id}",
            )
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
                    except asyncio.QueueFull:
                        log.warning(
                            "pool %s: inbox full, message lost in race",
                            pool.pool_id,
                        )
                    except Exception:
                        log.warning(
                            "pool %s: unexpected error in inbox race",
                            pool.pool_id,
                            exc_info=True,
                        )
                # Propagate CancelledError from /stop.
                if agent_task.cancelled():
                    raise asyncio.CancelledError
                return msg

            # New message arrived while agent was processing → cancel-in-flight.
            new_msg = inbox_waiter.result()
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

            msg = MessageDebouncer.merge(buffer)

    # ------------------------------------------------------------------
    # Guarded execution (timeout + error handling + events)
    # ------------------------------------------------------------------

    async def _guarded_process_one(  # noqa: C901 — event emission branches add inherent complexity
        self, msg: InboundMessage, agent: AgentBase
    ) -> None:
        """Wrap _process_one with timeout and error handling."""
        pool = self._pool
        from .event_bus import get_event_bus
        from .events import AgentCompleted, AgentFailed, AgentIdle, AgentStarted

        _start = time.monotonic()
        _cancelled = False
        if bus := get_event_bus():
            bus.emit(
                AgentStarted(
                    agent_id=pool.agent_name,
                    pool_id=pool.pool_id,
                    scope_id=msg.scope_id,
                )
            )
        try:
            if pool._turn_timeout is not None:
                await asyncio.wait_for(
                    self._process_one(msg, agent), timeout=pool._turn_timeout
                )
            else:
                await self._process_one(msg, agent)
            if bus := get_event_bus():
                bus.emit(
                    AgentCompleted(
                        agent_id=pool.agent_name,
                        pool_id=pool.pool_id,
                        duration_ms=(time.monotonic() - _start) * 1000,
                    )
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
            await self._safe_dispatch(msg, Response(content=_reply))
            if bus := get_event_bus():
                bus.emit(
                    AgentFailed(
                        agent_id=pool.agent_name, pool_id=pool.pool_id, error="timeout"
                    )
                )
        except asyncio.CancelledError:
            _cancelled = True
            raise
        except Exception as exc:
            log.exception("unhandled error in pool %s: %s", pool.pool_id, exc)
            _reply = pool._msg("generic", GENERIC_ERROR_REPLY)
            await self._safe_dispatch(msg, Response(content=_reply))
            pool._ctx.record_circuit_failure(exc)
            if bus := get_event_bus():
                bus.emit(
                    AgentFailed(
                        agent_id=pool.agent_name,
                        pool_id=pool.pool_id,
                        error=str(exc)[:200],
                    )
                )
        finally:
            if not _cancelled:
                if bus := get_event_bus():
                    bus.emit(
                        AgentIdle(
                            agent_id=pool.agent_name,
                            pool_id=pool.pool_id,
                            finished_at=time.monotonic(),
                        )
                    )

    # ------------------------------------------------------------------
    # Single-turn execution
    # ------------------------------------------------------------------

    async def _process_one(self, msg: InboundMessage, agent: AgentBase) -> None:  # noqa: C901 — session-id update adds one branch
        """Run agent.process and dispatch result (streaming or non-streaming)."""
        pool = self._pool
        pool.append(msg)  # S1 — track identity and message count
        _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
        if _ensure_fn is not None:
            await _ensure_fn(pool)  # S3 — cache system prompt

        async def _intermediate_cb(turn_text: str) -> None:
            await self._safe_dispatch(
                msg,
                Response(content=f"⏳ {turn_text}", intermediate=True),
            )

        result = agent.process(msg, pool, on_intermediate=_intermediate_cb)
        if not isinstance(result, collections.abc.AsyncIterator):
            # Regular coroutine — await to get the actual result
            try:
                result = await result  # type: ignore[assignment]  # coroutine → Response|AsyncIterator
            except Exception as exc:
                pool._ctx.record_circuit_failure(exc)
                raise

        if isinstance(result, collections.abc.AsyncIterator):
            try:
                await pool._ctx.dispatch_streaming(msg, result)
                pool._ctx.record_circuit_success()
            except BaseException as exc:
                pool._ctx.record_circuit_failure(exc)
                raise
            # L1 — streaming turn marker; TODO(#67): capture full content.
            pool._observer.log_turn_async(
                role="assistant",
                platform=pool.medium or str(msg.platform),
                user_id=pool.user_id or msg.user_id,
                content="",
            )
            pool._observer.session_update_async(msg)
        else:
            pool._ctx.record_circuit_success()
            # Update session_id with the real Claude CLI session UUID before
            # persisting to ThreadStore — the initial value is a placeholder UUID.
            if isinstance(result, Response):
                _cli_session_id = result.metadata.get("session_id")
                if _cli_session_id:
                    pool.session_id = _cli_session_id
            await pool._ctx.dispatch_response(msg, result)
            # L1 — log assistant turn (issue #67); fire-and-forget
            if isinstance(result, Response):
                _reply_msg_id = result.metadata.get("reply_message_id")
                pool._observer.log_turn_async(
                    role="assistant",
                    platform=pool.medium or str(msg.platform),
                    user_id=pool.user_id or msg.user_id,
                    content=result.content,
                    reply_message_id=(
                        str(_reply_msg_id) if _reply_msg_id is not None else None
                    ),
                )
            pool._observer.session_update_async(msg)

        _compact_fn = getattr(agent, "compact", None)
        if _compact_fn is not None:
            await _compact_fn(pool)  # S5 — check compaction threshold after each turn

    # ------------------------------------------------------------------
    # Safe dispatch helper
    # ------------------------------------------------------------------

    async def _safe_dispatch(self, msg: InboundMessage, response: Response) -> None:
        pool = self._pool
        try:
            await asyncio.wait_for(
                pool._ctx.dispatch_response(msg, response),
                timeout=SAFE_DISPATCH_TIMEOUT,
            )
        except Exception as exc:
            log.exception("_safe_dispatch failed for pool %s: %s", pool.pool_id, exc)
