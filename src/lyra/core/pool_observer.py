from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import InboundMessage
    from .message_index import MessageIndex
    from .turn_store import TurnStore

log = logging.getLogger(__name__)


class PoolObserver:
    """Owns turn-logging and session-persistence concerns for a Pool.

    Pass ``pool_id`` and a ``session_id_fn`` callable so the observer always
    reads the *current* session ID from the pool rather than holding a stale
    copy (session ID changes on /clear).
    """

    def __init__(
        self,
        pool_id: str,
        session_id_fn: Callable[[], str],
    ) -> None:
        self._pool_id = pool_id
        self._session_id_fn = session_id_fn

        self._turn_store: TurnStore | None = None
        self._message_index: MessageIndex | None = None
        self._turn_logger: Callable[[str, InboundMessage], Awaitable[None]] | None = (
            None
        )
        self._session_update_fn: (
            Callable[[InboundMessage, str, str], Awaitable[None]] | None
        ) = None
        self._session_persisted: bool = False

    # ------------------------------------------------------------------
    # Registration helpers (replaces direct attribute assignment)
    # ------------------------------------------------------------------

    def register_turn_store(self, store: TurnStore) -> None:
        """Wire the TurnStore for L1 raw turn logging."""
        self._turn_store = store

    def register_message_index(self, store: MessageIndex) -> None:
        """Wire the MessageIndex for session routing on reply-to (#341)."""
        self._message_index = store

    def register_turn_logger(
        self, fn: Callable[[str, InboundMessage], Awaitable[None]]
    ) -> None:
        """Wire the per-message turn logger callback."""
        self._turn_logger = fn

    def register_session_update_fn(
        self, fn: Callable[[InboundMessage, str, str], Awaitable[None]]
    ) -> None:
        """Wire the session persistence callback."""
        self._session_update_fn = fn

    def reset_session_persisted(self) -> None:
        """Reset the persisted flag so the next turn triggers persistence again."""
        self._session_persisted = False

    # ------------------------------------------------------------------
    # Core observability helpers
    # ------------------------------------------------------------------

    def _fire_and_forget(self, coro: Awaitable[None], label: str) -> None:
        """Schedule *coro* as fire-and-forget; log errors; no-op without event loop."""
        try:
            task = asyncio.ensure_future(coro)

            def _on_done(t: asyncio.Task) -> None:
                if not t.cancelled() and t.exception():
                    log.error("%s: %s", label, t.exception())

            task.add_done_callback(_on_done)
        except RuntimeError:
            pass  # no running event loop (e.g. sync test context)

    def log_turn_async(  # noqa: PLR0913
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
        self._fire_and_forget(
            self._turn_store.log_turn(
                pool_id=self._pool_id,
                session_id=self._session_id_fn(),
                role=role,
                platform=platform,
                user_id=user_id,
                content=content,
                message_id=message_id,
                reply_message_id=reply_message_id,
            ),
            f"turn_store write failed (pool={self._pool_id} role={role})",
        )

    def session_update_async(self, msg: InboundMessage) -> None:
        """Fire-and-forget session persistence via callback; no-op if absent."""
        if self._session_update_fn is None or self._session_persisted:
            return
        self._session_persisted = True
        self._fire_and_forget(
            self._session_update_fn(msg, self._session_id_fn(), self._pool_id),
            f"session_update failed (pool={self._pool_id})",
        )

    def append(
        self,
        msg: InboundMessage,
        *,
        session_id: str,
    ) -> None:
        """Fire turn-logger and log user turn for *msg*.

        Called from Pool.append() after identity fields are updated.
        ``session_id`` is passed explicitly so the observer uses the value
        that was current at the moment append() ran.
        """
        with contextlib.suppress(RuntimeError):
            if self._turn_logger is not None:
                _coro = self._turn_logger(session_id, msg)
                asyncio.create_task(_coro)  # type: ignore[arg-type]
        self.log_turn_async(
            role="user",
            platform=str(msg.platform),
            user_id=msg.user_id,
            content=msg.text,
            message_id=msg.id,
        )
        # Index user turn for reply-to session routing (#341).
        if self._message_index is not None:
            _msg_id = msg.platform_meta.get("message_id")
            if _msg_id is not None:
                self._fire_and_forget(
                    self._message_index.upsert(
                        self._pool_id, str(_msg_id), session_id, "user"
                    ),
                    f"message_index upsert failed (pool={self._pool_id} role=user)",
                )
