from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from ..stores.message_index_protocol import MessageIndexProtocol
from ..stores.turn_store_protocol import TurnStoreProtocol

if TYPE_CHECKING:
    from lyra.transport.turn_publisher import TurnPublisher

    from ..messaging.message import InboundMessage

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

        self._turn_store: TurnStoreProtocol | None = None
        self._turn_publisher: TurnPublisher | None = None
        self._message_index: MessageIndexProtocol | None = None
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

    def register_turn_store(self, store: TurnStoreProtocol) -> None:
        """Wire the TurnStore for L1 raw turn logging (kept for read paths)."""
        self._turn_store = store

    def register_turn_publisher(self, publisher: TurnPublisher) -> None:
        """Wire the TurnPublisher for NATS-backed turn writes."""
        self._turn_publisher = publisher

    def register_message_index(self, store: MessageIndexProtocol) -> None:
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

    def has_session_update_fn(self) -> bool:
        """Check whether a session persistence callback is registered."""
        return self._session_update_fn is not None

    def reset_session_persisted(self) -> None:
        """Reset the persisted flag so the next turn triggers persistence again."""
        self._session_persisted = False

    # ------------------------------------------------------------------
    # Core observability helpers
    # ------------------------------------------------------------------

    async def end_session_async(self, session_id: str) -> None:
        """Publish end_session via TurnPublisher; no-op if not connected."""
        if self._turn_publisher is None:
            return
        try:
            # trace_id: use session_id as the lifecycle correlation key
            await self._turn_publisher.publish_end_session(
                pool_id=self._pool_id,
                session_id=session_id,
                platform="",  # unknown at this point — lifecycle event only
                user_id="",
                trace_id=session_id,
            )
        except Exception:
            log.error(
                "turn_publisher end_session failed (pool=%s session=%s)",
                self._pool_id,
                session_id,
                exc_info=True,
            )

    async def log_turn_async(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        *,
        role: str,
        platform: str,
        user_id: str,
        content: str,
        message_id: str | None = None,
        reply_message_id: str | None = None,
    ) -> None:
        """Publish turn via TurnPublisher; no-op if not connected."""
        if self._turn_publisher is None:
            return
        try:
            # trace_id: use message_id if non-empty (natural per-message key),
            # otherwise fall back to a fresh uuid4 hex
            import uuid

            trace_id = (message_id or "").strip() or uuid.uuid4().hex
            await self._turn_publisher.publish_log_turn(
                pool_id=self._pool_id,
                session_id=self._session_id_fn(),
                platform=platform,
                user_id=user_id,
                role=role,
                content=content,
                message_id=message_id or None,
                reply_message_id=reply_message_id,
                trace_id=trace_id,
            )
        except Exception:
            log.error(
                "turn_publisher write failed (pool=%s role=%s)",
                self._pool_id,
                role,
                exc_info=True,
            )

    async def session_update_async(self, msg: InboundMessage) -> None:
        """Await session persistence via callback; no-op if absent."""
        if self._session_update_fn is None or self._session_persisted:
            return
        self._session_persisted = True
        try:
            await self._session_update_fn(msg, self._session_id_fn(), self._pool_id)
        except Exception:
            log.error(
                "session_update failed (pool=%s)",
                self._pool_id,
                exc_info=True,
            )

    async def append(
        self,
        msg: InboundMessage,
        *,
        session_id: str,
    ) -> None:
        """Await turn-logger and log user turn for *msg*.

        Called from Pool.append() after identity fields are updated.
        ``session_id`` is passed explicitly so the observer uses the value
        that was current at the moment append() ran.
        """
        if self._turn_logger is not None:
            try:
                await self._turn_logger(session_id, msg)
            except Exception:
                log.error(
                    "turn_logger failed (pool=%s)",
                    self._pool_id,
                    exc_info=True,
                )
        await self.log_turn_async(
            role="user",
            platform=str(msg.platform),
            user_id=msg.user_id,
            content=msg.text,
            message_id=msg.id,
        )
        # Index user turn for reply-to session routing (#341).
        _msg_id = getattr(msg.platform_meta, "message_id", None)
        if _msg_id is not None:
            await self.index_turn_async(
                str(_msg_id), session_id=session_id, role="user"
            )

    async def index_turn_async(
        self,
        platform_msg_id: str | None,
        *,
        session_id: str,
        role: Literal["user", "assistant"],
    ) -> None:
        """Await message index upsert; no-op if not connected."""
        if self._message_index is None or platform_msg_id is None:
            return
        try:
            await self._message_index.upsert(
                self._pool_id, platform_msg_id, session_id, role
            )
        except Exception:
            log.error(
                "message_index upsert failed (pool=%s)",
                self._pool_id,
                exc_info=True,
            )
