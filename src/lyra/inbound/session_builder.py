"""SessionBuilder — injects session_id and session_update_fn into InboundMessage."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import SessionCtx


class SessionBuilder:
    """Builds session context for an inbound message.

    Reads ``SessionCtx.turn_store``, ``SessionCtx.thread_store``, and
    ``SessionCtx.thread_sessions_cache`` to resolve or create a session.
    Returns an updated ``InboundMessage`` with ``session_update_fn`` populated.

    Handles three cases without isinstance branches on platform type:
    - ``turn_store=None, thread_store=None`` — test / CLI (no session persistence)
    - ``turn_store, thread_store=None`` — Telegram (DM / group via TurnStore only)
    - ``turn_store, thread_store`` — Discord (DM priority + ThreadStore for
      owned threads)

    Stub: full logic implemented in Wave 4 (Slice 4).
    """

    async def build(self, msg: InboundMessage, ctx: SessionCtx) -> InboundMessage:
        """Resolve session and return *msg* enriched with session context."""
        raise NotImplementedError
