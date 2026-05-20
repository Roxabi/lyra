"""Router — pure routing decision (PROCESS / DROP) for inbound messages.

Platform rules
--------------
Telegram:
  - DM (``TelegramMeta.is_group is False``) → PROCESS
  - Group + mention (``is_group and msg.is_mention``) → PROCESS
  - Group + no mention → DROP

Discord:
  - DM (``guild_id is None`` and ``thread_id is None``) → PROCESS
  - Direct mention anywhere → PROCESS
  - In an owned thread (``thread_id in ctx.owned_threads``) → PROCESS
  - In a watch channel (``channel_id in ctx.watch_channels``) → PROCESS
  - Everything else → DROP

Unknown ``PlatformMeta`` subclass → DROP (conservative fallback).

No I/O, no exceptions — ``Router.decide`` is sync and pure.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import RouterCtx


class RouteDecision(Enum):
    """Routing outcome returned by ``Router.decide``."""

    PROCESS = "process"
    DROP = "drop"


class Router:
    """Decides whether an inbound message should be processed or dropped.

    Reads ``InboundMessage.platform_meta``, ``InboundMessage.is_mention``,
    ``RouterCtx.owned_threads``, and ``RouterCtx.watch_channels``.
    Sync and pure — no I/O, no side effects, never raises.
    """

    def decide(self, msg: InboundMessage, ctx: RouterCtx) -> RouteDecision:
        """Return ``PROCESS`` or ``DROP`` for *msg* given routing context *ctx*."""
        # Import here (not module level) to keep TYPE_CHECKING guards above clean,
        # and to satisfy the layer invariant: router.py must NOT import discord/aiogram.
        from lyra.core.messaging.message import DiscordMeta, TelegramMeta

        meta = msg.platform_meta

        # ── Telegram ─────────────────────────────────────────────────────────
        if isinstance(meta, TelegramMeta):
            # DM: not a group chat — always process.
            if not meta.is_group:
                return RouteDecision.PROCESS
            # Group: only process when the bot is directly mentioned.
            if msg.is_mention:
                return RouteDecision.PROCESS
            return RouteDecision.DROP

        # ── Discord ──────────────────────────────────────────────────────────
        if isinstance(meta, DiscordMeta):
            # DM: no guild AND no thread (a plain DM channel).
            is_dm = meta.guild_id is None and meta.thread_id is None
            if is_dm:
                return RouteDecision.PROCESS

            # Direct mention anywhere in a server or thread.
            if msg.is_mention:
                return RouteDecision.PROCESS

            # Owned thread: thread_id present and registered in the hot set.
            is_thread = meta.thread_id is not None
            if is_thread and meta.thread_id in ctx.owned_threads:
                return RouteDecision.PROCESS

            # Watch channel: designated channel where all messages are processed.
            if (
                ctx.watch_channels is not None
                and meta.channel_id in ctx.watch_channels
            ):
                return RouteDecision.PROCESS

            return RouteDecision.DROP

        # ── Unknown PlatformMeta subclass ─────────────────────────────────
        return RouteDecision.DROP
