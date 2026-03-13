from __future__ import annotations

import asyncio
import collections.abc
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .agent import AgentBase

from .message import GENERIC_ERROR_REPLY, InboundMessage, OutboundMessage, Response

log = logging.getLogger(__name__)

TURN_TIMEOUT_DEFAULT = 60.0
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

    def __init__(
        self,
        pool_id: str,
        agent_name: str,
        ctx: PoolContext,
        turn_timeout: float = TURN_TIMEOUT_DEFAULT,
    ) -> None:
        self.pool_id = pool_id
        self.agent_name = agent_name
        # TODO: decide eviction strategy before adding memory layer:
        #   option A: deque(maxlen=N) for sliding-window compaction
        #   option B: Pool.append(msg) mutator with compaction callback (0→3 cascade)
        self.history: list[InboundMessage] = []
        self.sdk_history: deque[dict] = deque()
        self.max_sdk_history: int = 50
        self._ctx = ctx
        self._turn_timeout = turn_timeout
        self._inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._current_task: asyncio.Task | None = None
        self._last_active: float = time.monotonic()

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

    def _msg(self, key: str, fallback: str) -> str:
        """Fetch a localised message, falling back to the given string."""
        result = self._ctx.get_message(key)
        return result if result is not None else fallback

    async def _process_loop(self) -> None:
        """Consume inbox sequentially until empty."""
        try:
            while True:
                try:
                    msg = self._inbox.get_nowait()
                except asyncio.QueueEmpty:
                    break
                agent = self._ctx.get_agent(self.agent_name)
                if agent is None:
                    log.error(
                        "no agent %r for pool %s — message dropped",
                        self.agent_name,
                        self.pool_id,
                    )
                    continue
                try:
                    await asyncio.wait_for(
                        self._process_one(msg, agent), timeout=self._turn_timeout
                    )
                except asyncio.TimeoutError:
                    _reply = self._msg(
                        "timeout", "Your request timed out. Please try again."
                    )
                    await self._safe_dispatch(msg, Response(content=_reply))
                except asyncio.CancelledError:
                    _reply = self._msg("cancelled", "Request cancelled.")
                    await asyncio.shield(
                        self._safe_dispatch(msg, Response(content=_reply))
                    )
                    raise
                except Exception as exc:
                    log.exception("unhandled error in pool %s: %s", self.pool_id, exc)
                    _reply = self._msg("generic", GENERIC_ERROR_REPLY)
                    await self._safe_dispatch(msg, Response(content=_reply))
                    self._ctx.record_circuit_failure(exc)
        finally:
            self._current_task = None

    async def _process_one(self, msg: InboundMessage, agent: "AgentBase") -> None:
        """Run agent.process and dispatch result. Records CB success.

        Accepts both streaming patterns:
        - async generator function (yields directly)
        - regular async def returning AsyncIterator or Response
        """
        result = agent.process(msg, self)
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
        else:
            self._ctx.record_circuit_success()
            await self._ctx.dispatch_response(msg, result)

    async def _safe_dispatch(self, msg: InboundMessage, response: Response) -> None:
        try:
            await asyncio.wait_for(
                self._ctx.dispatch_response(msg, response),
                timeout=SAFE_DISPATCH_TIMEOUT,
            )
        except Exception as exc:
            log.exception("_safe_dispatch failed for pool %s: %s", self.pool_id, exc)

    def extend_sdk_history(self, new_messages: list[dict]) -> None:
        """Append messages from an exchange and trim to cap."""
        self.sdk_history.extend(new_messages)
        while len(self.sdk_history) > self.max_sdk_history:
            self.sdk_history.popleft()
