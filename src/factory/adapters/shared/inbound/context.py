"""Shared InboundContext builders for platform adapters (#1931)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx

if TYPE_CHECKING:
    from factory.adapters.discord import DiscordAdapter
    from factory.adapters.telegram import TelegramAdapter
    from factory.adapters.web.web_adapter import WebAdapter


def build_telegram_inbound_ctx(
    adapter: "TelegramAdapter",
    *,
    ingest: Any = None,
) -> InboundContext:
    """InboundContext for Telegram text and voice paths."""
    return InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=set(),  # Telegram has no thread model; Router only reads
            watch_channels=None,
        ),
        session=SessionCtx(
            turn_store=None,
            thread_store=None,  # Telegram has no thread model
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
        ingest=ingest,
    )


def build_discord_inbound_ctx(
    adapter: "DiscordAdapter",
    *,
    ingest: Any = None,
) -> InboundContext:
    """InboundContext for Discord text and audio paths."""
    return InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=adapter._owned_threads,
            watch_channels=adapter._watch_channels if adapter._watch_channels else None,
        ),
        session=SessionCtx(
            turn_store=None,
            thread_store=adapter._thread_store,
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=adapter._circuit_registry,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=adapter._msg_manager,
        ),
        ingest=ingest,
    )


def build_web_inbound_ctx(adapter: "WebAdapter") -> InboundContext:
    """InboundContext for the web smoke text path.

    Web has no thread model, no attachment store, and no circuit registry or
    message catalog — those ``DispatchCtx`` fields are ``None`` (the Dispatcher
    and ``push_to_hub_guarded`` tolerate ``None`` for each).
    """
    return InboundContext(
        router=RouterCtx(
            bot_id=adapter._bot_id,
            owned_threads=set(),  # web has no thread model; Router only reads
            watch_channels=None,
        ),
        session=SessionCtx(
            turn_store=None,
            thread_store=None,  # web has no thread model
        ),
        dispatch=DispatchCtx(
            inbound_bus=adapter._inbound_bus,
            circuit_registry=None,
            outbound_listener=adapter._outbound_listener,
            typing=adapter._typing,
            msg_catalog=None,
        ),
        ingest=None,
    )
