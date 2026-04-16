from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.infrastructure.stores.turn_store import TurnStore

    from ..memory import SessionSnapshot

from ..debouncer import DEFAULT_DEBOUNCE_MS, MessageDebouncer
from ..message import InboundMessage, OutboundMessage
from .pool_context import PoolContext as PoolContext
from .pool_observer import PoolObserver
from .pool_processor import PoolProcessor

log = logging.getLogger(__name__)

TURN_TIMEOUT_DEFAULT: float | None = None  # CliPool handles liveness


class Pool:
    """One pool per conversation scope. Holds history and a per-session asyncio.Task."""

    def __init__(  # noqa: PLR0913
        self,
        pool_id: str,
        agent_name: str,
        ctx: PoolContext,
        turn_timeout: float | None = TURN_TIMEOUT_DEFAULT,
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
        turn_timeout_ceiling: float | None = None,
        max_sdk_history: int = 50,
        safe_dispatch_timeout: float = 10.0,
        max_merged_chars: int = 4096,
        cancel_on_new_message: bool = False,
    ) -> None:
        self.pool_id = pool_id
        self.agent_name = agent_name
        self.history: list[InboundMessage] = []
        self.sdk_history: deque[dict] = deque()
        self.max_sdk_history: int = max_sdk_history
        self._safe_dispatch_timeout: float = safe_dispatch_timeout
        self._session_reset_fn: Callable[[], Awaitable[None]] | None = None
        self._session_resume_fn: Callable[[str], Awaitable[bool]] | None = None
        self._on_resume_fn: Callable[[str], Awaitable[None]] | None = None  # set once
        self._switch_workspace_fn: Callable[[Path], Awaitable[None]] | None = None
        self._ctx = ctx
        # Ceiling clamp: use ceiling as default, clamp agent override to ceiling
        if turn_timeout is not None and turn_timeout_ceiling is not None:
            if turn_timeout > turn_timeout_ceiling:
                log.warning(
                    "[pool:%s] turn_timeout %.0fs > ceiling %.0fs — clamped",
                    pool_id,
                    turn_timeout,
                    turn_timeout_ceiling,
                )
                turn_timeout = turn_timeout_ceiling
            self._turn_timeout = turn_timeout
        elif turn_timeout is not None:
            self._turn_timeout = turn_timeout
        elif turn_timeout_ceiling is not None:
            self._turn_timeout = turn_timeout_ceiling
        else:
            self._turn_timeout = None
        self._debouncer = MessageDebouncer(debounce_ms, max_merged_chars)
        self._cancel_on_new_message: bool = cancel_on_new_message
        self._inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._current_task: asyncio.Task | None = None
        self._inflight_stream_outbound: OutboundMessage | None = None
        self._last_active: float = time.monotonic()
        self.session_id: str = str(uuid.uuid4())  # S1 (issue #83)
        self.user_id: str = ""
        self.medium: str = ""
        self.session_start: datetime = datetime.now(UTC)
        self.message_count: int = 0
        self._system_prompt: str = ""
        self.voice_mode: bool = False
        self.last_detected_language: str | None = None
        self._last_turn_had_backend_error: bool = False
        self._last_msg: InboundMessage | None = None
        # set by pipeline when pool busy on reply-to
        self._pending_session_id: str | None = None
        self._observer = PoolObserver(
            pool_id=pool_id,
            session_id_fn=lambda: self.session_id,
        )
        self._processor = PoolProcessor(self)

    # Backward-compat shims — delegate to observer so callers keep working.
    @property
    def _turn_store(self) -> TurnStore | None:
        return self._observer._turn_store

    @_turn_store.setter
    def _turn_store(self, value: TurnStore | None) -> None:
        self._observer._turn_store = value

    @property
    def _turn_logger(
        self,
    ) -> Callable[[str, InboundMessage], Awaitable[None]] | None:
        return self._observer._turn_logger

    @_turn_logger.setter
    def _turn_logger(
        self, value: Callable[[str, InboundMessage], Awaitable[None]] | None
    ) -> None:
        self._observer._turn_logger = value

    @property
    def _session_update_fn(
        self,
    ) -> Callable[[InboundMessage, str, str], Awaitable[None]] | None:
        return self._observer._session_update_fn

    @_session_update_fn.setter
    def _session_update_fn(
        self, value: Callable[[InboundMessage, str, str], Awaitable[None]] | None
    ) -> None:
        self._observer._session_update_fn = value

    @property
    def _session_persisted(self) -> bool:
        return self._observer._session_persisted

    @_session_persisted.setter
    def _session_persisted(self, value: bool) -> None:
        self._observer._session_persisted = value

    @property
    def debounce_ms(self) -> int:
        """Current debounce window in milliseconds."""
        return self._debouncer.debounce_ms

    @debounce_ms.setter
    def debounce_ms(self, value: int) -> None:
        """Update debounce window on the live debouncer."""
        self._debouncer.debounce_ms = value

    @property
    def cancel_on_new_message(self) -> bool:
        """Whether a new message cancels an in-flight LLM turn (cancel-in-flight).

        Default is False: new messages queue naturally and are processed after
        the current turn finishes.  Set to True to restore legacy cancel-in-flight
        behaviour where a new message aborts the ongoing turn and re-dispatches
        the merged context.
        """
        return self._cancel_on_new_message

    @cancel_on_new_message.setter
    def cancel_on_new_message(self, value: bool) -> None:
        """Toggle cancel-in-flight on the live pool (takes effect on next turn)."""
        self._cancel_on_new_message = value

    def has_session_update_fn(self) -> bool:
        """Check whether a session persistence callback is registered."""
        return self._observer.has_session_update_fn()

    def register_session_callbacks(
        self,
        *,
        reset_fn: Callable[[], Awaitable[None]] | None = None,
        resume_fn: Callable[[str], Awaitable[bool]] | None = None,
        workspace_fn: Callable[[Path], Awaitable[None]] | None = None,
        update_fn: Callable[[InboundMessage, str, str], Awaitable[None]] | None = None,
    ) -> None:
        """Wire session callbacks. Each is registered only if not already set."""
        if reset_fn is not None and self._session_reset_fn is None:
            self._session_reset_fn = reset_fn
        if resume_fn is not None and self._session_resume_fn is None:
            self._session_resume_fn = resume_fn
        if workspace_fn is not None and self._switch_workspace_fn is None:
            self._switch_workspace_fn = workspace_fn
        if update_fn is not None and not self._observer.has_session_update_fn():
            self._observer.register_session_update_fn(update_fn)

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
                self._processor.process_loop(), name=f"pool:{self.pool_id}"
            )

    @property
    def is_idle(self) -> bool:
        """Return True if the pool has no active processing task."""
        return self._current_task is None or self._current_task.done()

    def cancel(self) -> None:
        """Cancel the current processing task (no-op if idle)."""
        if self._current_task is not None and not self._current_task.done():
            self._current_task.cancel()

    async def resume_session(self, session_id: str) -> bool:
        """Resume a specific Claude session (CLI backend). Returns True if accepted.
        Resets _session_persisted only when accepted so the resumed session_id is
        re-persisted on the next turn (#341).
        """
        if self._session_resume_fn is not None:
            accepted = await self._session_resume_fn(session_id)
        else:
            log.warning(
                "[pool:%s] resume_session: no resume callback registered"
                " — skipping resume of %r (SDK pool or misconfigured agent)",
                self.pool_id,
                session_id,
            )
            accepted = False
        if accepted:
            self._observer.reset_session_persisted()
            if self._on_resume_fn is not None:
                try:
                    await self._on_resume_fn(session_id)
                except Exception:
                    log.exception(
                        "[pool:%s] resume count increment failed for %r",
                        self.pool_id,
                        session_id,
                    )
        return accepted

    def _msg(self, key: str, fallback: str) -> str:
        """Fetch a localised message, falling back to the given string."""
        result = self._ctx.get_message(key)
        return result if result is not None else fallback

    async def reset_session(self) -> None:
        """Reset session state; called by /clear. Rotates UUID, notifies TurnStore."""
        old_sid = self.session_id
        await self._observer.end_session_async(old_sid)
        self.session_id = str(uuid.uuid4())
        if self._observer._turn_store is not None:
            try:
                await self._observer._turn_store.start_session(
                    self.session_id, self.pool_id
                )
            except Exception:
                log.exception("[pool:%s] start_session failed", self.pool_id)
        self._observer.reset_session_persisted()
        if self._session_reset_fn is not None:
            await self._session_reset_fn()

    async def switch_workspace(self, cwd: Path) -> None:
        """Switch workspace cwd; no-op for SDK-backed agents (CLI concept only)."""
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

    async def append(self, msg: InboundMessage) -> None:
        """Called from _process_one. Promotes session identity and tracks count."""
        if self.user_id == "":
            self.user_id = msg.user_id
            self.medium = str(msg.platform)
        self.message_count += 1
        self._last_msg = msg
        await self._observer.append(msg, session_id=self.session_id)

    def snapshot(self, agent_namespace: str) -> "SessionSnapshot":
        from ..memory import SessionSnapshot

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
