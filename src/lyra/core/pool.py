from __future__ import annotations

import asyncio
import collections.abc
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from lyra.errors import ProviderError

if TYPE_CHECKING:
    from .agent import AgentBase
    from .hub import Hub

from .message import GENERIC_ERROR_REPLY, InboundMessage, Response

log = logging.getLogger(__name__)

TURN_TIMEOUT_DEFAULT = 60.0
SAFE_DISPATCH_TIMEOUT = 10.0


class Pool:
    """One pool per conversation scope. Holds history and a per-session asyncio.Task."""

    def __init__(
        self,
        pool_id: str,
        agent_name: str,
        hub: "Hub",
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
        self._hub = hub
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
        return self._hub._msg_manager.get(key) if self._hub._msg_manager else fallback

    async def _process_loop(self) -> None:
        """Consume inbox sequentially until empty."""
        try:
            while True:
                try:
                    msg = self._inbox.get_nowait()
                except asyncio.QueueEmpty:
                    break
                agent = self._hub.agent_registry.get(self.agent_name)
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
                    if self._hub.circuit_registry is not None:
                        _cb = self._hub.circuit_registry.get("hub")
                        if _cb is not None:
                            _cb.record_failure()
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
                self._record_cb_failure(exc)
                raise

        if isinstance(result, collections.abc.AsyncIterator):
            try:
                await self._hub.dispatch_streaming(msg, result)
                self._record_cb_success()
            except BaseException as exc:
                self._record_cb_failure(exc)
                raise
        else:
            self._record_cb_success()
            await self._hub.dispatch_response(msg, result)

    def _record_cb_success(self) -> None:
        if self._hub.circuit_registry is not None:
            for name in ("anthropic", "hub"):
                cb = self._hub.circuit_registry.get(name)
                if cb is not None:
                    cb.record_success()

    def _record_cb_failure(self, exc: BaseException) -> None:
        if self._hub.circuit_registry is not None:
            _hub_cb = self._hub.circuit_registry.get("hub")
            if _hub_cb is not None:
                _hub_cb.record_failure()
            if isinstance(exc, ProviderError):
                _ant_cb = self._hub.circuit_registry.get("anthropic")
                if _ant_cb is not None:
                    _ant_cb.record_failure()

    async def _safe_dispatch(self, msg: InboundMessage, response: Response) -> None:
        try:
            await asyncio.wait_for(
                self._hub.dispatch_response(msg, response),
                timeout=SAFE_DISPATCH_TIMEOUT,
            )
        except Exception as exc:
            log.exception("_safe_dispatch failed for pool %s: %s", self.pool_id, exc)

    def extend_sdk_history(self, new_messages: list[dict]) -> None:
        """Append messages from an exchange and trim to cap."""
        self.sdk_history.extend(new_messages)
        while len(self.sdk_history) > self.max_sdk_history:
            self.sdk_history.popleft()
