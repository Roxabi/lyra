"""SessionBuilder — injects session_id and session_update_fn into InboundMessage."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from factory.core.messaging.message import DiscordMeta

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import SessionCtx

log = logging.getLogger("factory.inbound.session_builder")


class SessionBuilder:
    """Builds session context for an inbound message.

    Reads ``SessionCtx.thread_store`` to resolve or create a session.
    Returns an updated ``InboundMessage`` with ``session_update_fn`` populated.

    Handles two cases without importing platform libraries:

    (a) ``thread_store`` None — test/CLI: no session persistence, return msg
        unchanged.
    (d) thread_store present, Discord owned thread — claim-routing only;
        resume is hub-side path-3 via ``scope_id=thread:{thread_id}``.

    The ``session_update_fn`` closure captures stores and IDs by reference and is
    safe to call after ``build`` returns — the inbound turn handler invokes it once
    a session_id is assigned.

    Platform meta discrimination uses ``isinstance(meta, DiscordMeta)`` to distinguish
    Discord from Telegram/Generic; within Discord, ``meta.guild_id`` and
    ``meta.thread_id`` determine DM vs thread path.
    """

    async def build(self, msg: InboundMessage, ctx: SessionCtx) -> InboundMessage:
        """Resolve session and return *msg* enriched with session context.

        Returns the original *msg* unchanged when ``thread_store`` is None (path a).
        Otherwise returns a new ``InboundMessage`` via ``dataclasses.replace``
        with ``session_update_fn`` set.
        """
        th = ctx.thread_store
        meta = msg.platform_meta

        # (a) No stores — test/CLI mode; nothing to inject.
        if th is None:
            return msg

        # Discriminate path: Discord with thread_store present uses meta inspection.
        _discord_thread = isinstance(meta, DiscordMeta) and meta.thread_id is not None
        if _discord_thread:
            # (d) Discord owned thread — claim-routing only; resume is hub-side path-3.
            return await self._build_thread_path(msg, ctx)

        return msg

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_thread_path(
        self, msg: InboundMessage, ctx: SessionCtx
    ) -> InboundMessage:
        """Path (d): Discord owned thread — claim-routing only.

        Discord thread is its own pool; resume is hub-side path-3 via
        ``scope_id=thread:{thread_id}``.  Session persistence is no longer
        performed here (#1777).
        """
        th = ctx.thread_store
        assert th is not None  # guarded by caller

        meta = msg.platform_meta
        assert isinstance(meta, DiscordMeta)  # guarded by caller
        assert meta.thread_id is not None  # guarded by caller

        # Capture by value so the closure is safe after build() returns.
        _th = th
        _bid = msg.bot_id

        async def _thread_update_fn(
            _msg: InboundMessage, session_id: str, pool_id: str
        ) -> None:
            # Closure captures _th, _bid and is called by the turn handler after
            # a session_id has been assigned.
            # Thread-path resume is now hub-side (path-3); no store write here.
            _ = (_th, _bid, session_id, pool_id)  # suppress unused-capture lint

        return dataclasses.replace(msg, session_update_fn=_thread_update_fn)
