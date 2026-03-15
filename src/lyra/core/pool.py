from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .agent import AgentBase
    from .memory import SessionSnapshot
    from .turn_store import TurnStore

from .debouncer import DEFAULT_DEBOUNCE_MS, MessageDebouncer
from .message import GENERIC_ERROR_REPLY, InboundMessage, OutboundMessage, Response

log = logging.getLogger(__name__)

TURN_TIMEOUT_DEFAULT: float | None = None  # CliPool handles liveness
SAFE_DISPATCH_TIMEOUT = 10.0


@runtime_checkable
class PoolContext(Protocol):
    """Narrow interface that Pool needs from its owner (typically Hub).

    Decouples Pool from the full Hub, enabling isolated testing and
    reducing coupling (ADR-017, issue #204).
    """

    def get_agent(self, name: str) -> AgentBase | None: ...

    def get_message(self, key: str) -> str | None: ...

    async def dispatch_response(
        self, msg: InboundMessage, response: Response | OutboundMessage
    ) -> None: ...

    async def dispatch_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None: ...

    def record_circuit_success(self) -> None: ...

    def record_circuit_failure(self, exc: BaseException) -> None: ...


class Pool:
    """One pool per conversation scope. Holds history and a per-session asyncio.Task."""

    _session_reset_fn: Callable[[], Awaitable[None]] | None
    _session_resume_fn: Callable[[str], Awaitable[None]] | None
    _switch_workspace_fn: Callable[[Path], Awaitable[None]] | None

    def __init__(
        self,
        pool_id: str,
        agent_name: str,
        ctx: PoolContext,
        turn_timeout: float | None = TURN_TIMEOUT_DEFAULT,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    ) -> None:
        self.pool_id = pool_id
        self.agent_name = agent_name
        # TODO: decide eviction strategy before adding memory layer:
        #   option A: deque(maxlen=N) for sliding-window compaction
        #   option B: Pool.append(msg) mutator with compaction callback (0→3 cascade)
        self.history: list[InboundMessage] = []
        self.sdk_history: deque[dict] = deque()
        self.max_sdk_history: int = 50
        self._session_reset_fn: Callable[[], Awaitable[None]] | None = None
        self._session_resume_fn: Callable[[str], Awaitable[None]] | None = None
        self._switch_workspace_fn: Callable[[Path], Awaitable[None]] | None = None
        self._ctx = ctx
        self._turn_timeout = turn_timeout
        self._debouncer = MessageDebouncer(debounce_ms)
        self._inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._current_task: asyncio.Task | None = None
        self._last_active: float = time.monotonic()
        self.session_id: str = str(uuid.uuid4())  # S1 (issue #83)
        self.user_id: str = ""
        self.medium: str = ""
        self.session_start: datetime = datetime.now(UTC)
        self.message_count: int = 0
        self._system_prompt: str = ""
        self._turn_logger: (
            Callable[[str, InboundMessage], Awaitable[None]] | None
        ) = None
        self._turn_store: "TurnStore | None" = None  # L1 (issue #67)

    @property
    def debounce_ms(self) -> int:
        """Current debounce window in milliseconds."""
        return self._debouncer.debounce_ms

    @debounce_ms.setter
    def debounce_ms(self, value: int) -> None:
        """Update debounce window on the live debouncer."""
        self._debouncer.debounce_ms = value

    @property
    def last_active(self) -> float:
        """Monotonic timestamp of last activity (read-only for external callers)."""
        return self._last_active

    def _touch(self) -> None:
        """Refresh last_active to now."""
        self._last_active = time.monotonic()

    def submit(self, msg: InboundMessage) -> None:
        """Enqueue msg; start processing task if not running."""
        self._touch()
        self._inbox.put_nowait(msg)
        if self._current_task is None or self._current_task.done():
            self._current_task = asyncio.create_task(
                self._process_loop(), name=f"pool:{self.pool_id}"
            )

    @property
    def is_idle(self) -> bool:
        """Return True if the pool has no active processing task."""
        return self._current_task is None or self._current_task.done()

    def cancel(self) -> None:
        """Cancel the current processing task (no-op if idle)."""
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    async def resume_session(self, session_id: str) -> None:
        """Resume a specific Claude session (CLI backend).

        No-op for SDK-backed pools.
        """
        if self._session_resume_fn is not None:
            await self._session_resume_fn(session_id)

    def _msg(self, key: str, fallback: str) -> str:
        """Fetch a localised message, falling back to the given string."""
        result = self._ctx.get_message(key)
        return result if result is not None else fallback

    async def _process_loop(self) -> None:  # noqa: C901 — debounce + cancel-in-flight adds inherent branches
        """Consume inbox with debounce aggregation and cancel-in-flight."""
        _last_msg: InboundMessage | None = None
        try:
            while True:
                if self._inbox.empty():
                    break
                buffer = await self._debouncer.collect(self._inbox)

                agent = self._ctx.get_agent(self.agent_name)
                if agent is None:
                    log.error(
                        "no agent %r for pool %s — message(s) dropped",
                        self.agent_name,
                        self.pool_id,
                    )
                    # Drain any remaining queued messages before exiting
                    while not self._inbox.empty():
                        try:
                            self._inbox.get_nowait()
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
                _reply = self._msg("cancelled", "Request cancelled.")
                await asyncio.shield(
                    self._safe_dispatch(_last_msg, Response(content=_reply))
                )
            raise
        finally:
            self._current_task = None

    async def _process_with_cancel(
        self,
        msg: InboundMessage,
        buffer: list[InboundMessage],
        agent: "AgentBase",
    ) -> InboundMessage:
        """Run agent.process(), cancelling and re-dispatching if new messages arrive."""
        while True:
            agent_task = asyncio.create_task(
                self._guarded_process_one(msg, agent),
                name=f"agent:{self.pool_id}",
            )
            inbox_waiter = asyncio.create_task(
                self._inbox.get(), name=f"inbox:{self.pool_id}"
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
                        self._inbox.put_nowait(inbox_waiter.result())
                    except asyncio.QueueFull:
                        log.warning(
                            "pool %s: inbox full, message lost in race",
                            self.pool_id,
                        )
                    except Exception:
                        log.warning(
                            "pool %s: unexpected error in inbox race",
                            self.pool_id,
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
                self.pool_id,
            )

            # Debounce the new message (drain any rapid follow-ups).
            buffer.append(new_msg)
            buffer.extend(await self._debouncer.drain_followups(self._inbox))

            msg = MessageDebouncer.merge(buffer)

    async def _guarded_process_one(  # noqa: C901 — event emission branches add inherent complexity
        self, msg: InboundMessage, agent: "AgentBase"
    ) -> None:
        """Wrap _process_one with timeout and error handling."""
        from .event_bus import get_event_bus
        from .events import AgentCompleted, AgentFailed, AgentIdle, AgentStarted

        _start = time.monotonic()
        _cancelled = False
        if bus := get_event_bus():
            bus.emit(
                AgentStarted(
                    agent_id=self.agent_name,
                    pool_id=self.pool_id,
                    scope_id=msg.scope_id,
                )
            )
        try:
            if self._turn_timeout is not None:
                await asyncio.wait_for(
                    self._process_one(msg, agent), timeout=self._turn_timeout
                )
            else:
                await self._process_one(msg, agent)
            if bus := get_event_bus():
                bus.emit(
                    AgentCompleted(
                        agent_id=self.agent_name,
                        pool_id=self.pool_id,
                        duration_ms=(time.monotonic() - _start) * 1000,
                    )
                )
        except asyncio.TimeoutError:
            log.warning(
                "pool %s: turn timeout after %.0fs — killing backend",
                self.pool_id,
                self._turn_timeout,
            )
            if not agent.is_backend_alive(self.pool_id):
                log.error(
                    "pool %s: backend process died — timeout caused by dead process",
                    self.pool_id,
                )
            await agent.reset_backend(self.pool_id)
            _reply = self._msg("timeout", "Your request timed out. Please try again.")
            await self._safe_dispatch(msg, Response(content=_reply))
            if bus := get_event_bus():
                bus.emit(
                    AgentFailed(
                        agent_id=self.agent_name, pool_id=self.pool_id, error="timeout"
                    )
                )
        except asyncio.CancelledError:
            # Distinguish /stop cancellation (from pool.cancel()) vs
            # debounce cancel-in-flight.  Cancel-in-flight catches this
            # inside _process_with_cancel; /stop propagates outward.
            _cancelled = True
            raise
        except Exception as exc:
            log.exception("unhandled error in pool %s: %s", self.pool_id, exc)
            _reply = self._msg("generic", GENERIC_ERROR_REPLY)
            await self._safe_dispatch(msg, Response(content=_reply))
            self._ctx.record_circuit_failure(exc)
            if bus := get_event_bus():
                bus.emit(
                    AgentFailed(
                        agent_id=self.agent_name,
                        pool_id=self.pool_id,
                        error=str(exc)[:200],
                    )
                )
        finally:
            if not _cancelled:
                if bus := get_event_bus():
                    bus.emit(
                        AgentIdle(
                            agent_id=self.agent_name,
                            pool_id=self.pool_id,
                            finished_at=time.monotonic(),
                        )
                    )

    async def _process_one(self, msg: InboundMessage, agent: "AgentBase") -> None:
        """Run agent.process and dispatch result (streaming or non-streaming)."""
        self.append(msg)  # S1 — track identity and message count
        _ensure_fn = getattr(agent, "_ensure_system_prompt", None)
        if _ensure_fn is not None:
            await _ensure_fn(self)  # S3 — cache system prompt

        async def _intermediate_cb(turn_text: str) -> None:
            await self._safe_dispatch(
                msg,
                Response(content=f"⏳ {turn_text}", intermediate=True),
            )

        result = agent.process(msg, self, on_intermediate=_intermediate_cb)
        if not isinstance(result, collections.abc.AsyncIterator):
            # Regular coroutine — await to get the actual result
            try:
                result = await result  # type: ignore[assignment]  # coroutine → Response|AsyncIterator
            except Exception as exc:
                self._ctx.record_circuit_failure(exc)
                raise

        if isinstance(result, collections.abc.AsyncIterator):
            try:
                await self._ctx.dispatch_streaming(msg, result)
                self._ctx.record_circuit_success()
            except BaseException as exc:
                self._ctx.record_circuit_failure(exc)
                raise
            # L1 — streaming turn marker; TODO(#67): capture full content.
            self._log_turn_async(
                role="assistant",
                platform=self.medium or str(msg.platform),
                user_id=self.user_id or msg.user_id,
                content="",
            )
        else:
            self._ctx.record_circuit_success()
            await self._ctx.dispatch_response(msg, result)
            # L1 — log assistant turn (issue #67); fire-and-forget
            if isinstance(result, Response):
                _reply_msg_id = result.metadata.get("reply_message_id")
                self._log_turn_async(
                    role="assistant",
                    platform=self.medium or str(msg.platform),
                    user_id=self.user_id or msg.user_id,
                    content=result.content,
                    reply_message_id=(
                        str(_reply_msg_id) if _reply_msg_id is not None else None
                    ),
                )

        _compact_fn = getattr(agent, "compact", None)
        if _compact_fn is not None:
            await _compact_fn(self)  # S5 — check compaction threshold after each turn

    async def _safe_dispatch(self, msg: InboundMessage, response: Response) -> None:
        try:
            await asyncio.wait_for(
                self._ctx.dispatch_response(msg, response),
                timeout=SAFE_DISPATCH_TIMEOUT,
            )
        except Exception as exc:
            log.exception("_safe_dispatch failed for pool %s: %s", self.pool_id, exc)

    async def reset_session(self) -> None:
        """Reset external session state (e.g. CLI session_id).

        Called by /clear. Clears in-memory history then delegates to any
        registered reset callback (e.g. ClaudeCliDriver killing its process).
        """
        if self._session_reset_fn is not None:
            await self._session_reset_fn()

    async def switch_workspace(self, cwd: Path) -> None:
        """Switch workspace cwd, kill process, clear history.

        No-op for SDK-backed agents (where _switch_workspace_fn is None)
        — cwd is a CLI concept and history should not be silently wiped
        on SDK backends.
        """
        if self._switch_workspace_fn is None:
            return
        self.sdk_history.clear()
        self.history.clear()
        await self._switch_workspace_fn(cwd)

    def extend_sdk_history(self, new_messages: list[dict]) -> None:
        """Append messages from an exchange and trim to cap."""
        self.sdk_history.extend(new_messages)
        while len(self.sdk_history) > self.max_sdk_history:
            self.sdk_history.popleft()

    # S1 — session identity mutators (issue #83)

    def append(self, msg: InboundMessage) -> None:
        """Called from _process_one. Promotes session identity and tracks count."""
        if self.user_id == "":
            self.user_id = msg.user_id
            # msg.platform is a str at runtime (platform.value already resolved)
            self.medium = str(msg.platform)
        self.message_count += 1
        if self._turn_logger is not None:
            try:
                asyncio.create_task(self._turn_logger(self.session_id, msg))  # type: ignore[arg-type]
            except RuntimeError:
                pass  # no running event loop (e.g. sync test context)
        self._log_turn_async(
            role="user",
            platform=str(msg.platform),
            user_id=msg.user_id,
            content=msg.text,
            message_id=msg.id,
        )

    def _log_turn_async(  # noqa: PLR0913
        self,
        *,
        role: str,
        platform: str,
        user_id: str,
        content: str,
        message_id: str | None = None,
        reply_message_id: str | None = None,
    ) -> None:
        """Fire-and-forget turn logging via TurnStore; no-op if not connected."""
        if self._turn_store is None:
            return
        try:
            task = asyncio.create_task(
                self._turn_store.log_turn(
                    pool_id=self.pool_id,
                    session_id=self.session_id,
                    role=role,
                    platform=platform,
                    user_id=user_id,
                    content=content,
                    message_id=message_id,
                    reply_message_id=reply_message_id,
                )
            )

            def _cb(t: asyncio.Task) -> None:
                if not t.cancelled() and t.exception():
                    log.error(
                        "turn_store write failed (pool=%s role=%s): %s",
                        self.pool_id, role, t.exception(),
                    )

            task.add_done_callback(_cb)
        except RuntimeError:
            pass  # no running event loop (e.g. sync test context)

    def snapshot(self, agent_namespace: str) -> "SessionSnapshot":
        from .memory import SessionSnapshot

        return SessionSnapshot(
            session_id=self.session_id,
            user_id=self.user_id,
            medium=self.medium,
            agent_namespace=agent_namespace,
            session_start=self.session_start,
            session_end=datetime.now(UTC),
            message_count=self.message_count,
            source_turns=len(self.sdk_history),
        )
