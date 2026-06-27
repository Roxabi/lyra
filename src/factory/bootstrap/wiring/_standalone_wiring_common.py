"""Shared wiring logic for standalone adapter bootstraps (Telegram + Discord).

Extracted from standalone_telegram.py and standalone_discord.py to eliminate the
85% duplication in their _wire_bot closures. The per-platform modules keep only
their setup/teardown and delegate the common wiring sequence here.

RC: both _wire_bot closures shared NatsBus creation → adapter construction →
NatsOutboundListener wiring → TypingListener setup → start_audio_consumer, with
only the adapter/typing construction and resolve_identity call differing by platform.

Correction class: Archi (structural — new shared module, not a local patch).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from factory.adapters.nats.nats_outbound_listener import (
    ListenerDeps,
    NatsOutboundListener,
)
from factory.bootstrap.lifecycle.lifecycle_helpers import (
    close_safely,
    run_with_teardown,
)
from factory.bootstrap.standalone.audio_consumer_bootstrap import start_audio_consumer
from factory.bootstrap.wiring.bootstrap_wiring import wire_ingest
from factory.core.messaging.bus import Bus
from factory.core.messaging.message import InboundMessage, Platform
from factory.nats.nats_bus import NatsBus
from factory.nats.queue_groups import adapter_outbound
from factory.transport.typing_publisher import TypingPublisher
from factory.typing import TypingListener, make_typing_factory

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


@dataclass
class TypingDeps:
    """Platform-specific typing listener wiring deps."""

    subject: str
    scope_resolver: Any
    worker_factory: Callable[..., Any]


async def wire_bot_common(  # noqa: PLR0913 — wiring root: nc + platform + bot_id + factory + config + js + blobstore + flag
    *,
    nc: Any,
    platform_enum: Platform,
    bot_id: str,
    adapter_factory: Callable[[Bus[InboundMessage]], tuple[Any, TypingDeps]],
    config_bundle: Any,
    js: "JetStreamContext",
    blob_store: Any,
    resolve_identity: bool = False,
) -> tuple:
    """Wire a single bot against NATS: bus → adapter → listener → typing → audio.

    This helper encapsulates the wiring sequence shared by both Telegram and
    Discord standalone bootstraps.  Callers supply an ``adapter_factory`` that
    receives the pre-started ``NatsBus`` and returns ``(adapter, TypingDeps)``.
    This lets the caller construct the platform-specific adapter with the bus
    reference, and build typing deps that reference adapter internals (e.g.
    ``adapter.bot`` for Telegram's typing worker).

    Args:
        nc:              Live NATS connection.
        platform_enum:   Platform enum value (TELEGRAM or DISCORD).
        bot_id:          Bot identifier string.
        adapter_factory: Callable ``(inbound_bus) -> (adapter, TypingDeps)``.
                         Called after the bus is started.
        config_bundle:   AdapterConfigBundle (provides tool_display).
        js:              JetStreamContext for the audio consumer.
        blob_store:      BlobStore instance (may be None).
        resolve_identity: Whether to call ``adapter.resolve_identity()`` (TG only).

    Returns:
        ``(adapter, inbound_bus, typing_listener, consumer)``

    Raises:
        Any exception from ``astart()`` or ``typing_listener.start()`` after
        cleaning up the resources opened so far.
    """
    inbound_bus: Bus[InboundMessage] = NatsBus(
        nc=nc,
        bot_id=bot_id,
        item_type=InboundMessage,
        publish_only=True,
    )
    inbound_bus.register(platform_enum)
    await inbound_bus.start()

    adapter, typing_deps = adapter_factory(inbound_bus)

    adapter.configure_tool_display(config_bundle.tool_display)
    adapter.configure_typing_publisher(TypingPublisher(nc))
    if resolve_identity:
        await adapter.resolve_identity()
    wire_ingest(adapter, blob_store)

    listener = NatsOutboundListener(
        ListenerDeps(
            nc=nc,
            platform=platform_enum,
            bot_id=bot_id,
            adapter=adapter,
            queue_group=adapter_outbound(platform_enum.value, bot_id),
        )
    )
    adapter._outbound_listener = listener
    async def _teardown_adapter_start() -> None:
        await close_safely(
            f"{platform_enum.value}-adapter-start",
            adapter.close(),
            inbound_bus.stop(),
        )

    await run_with_teardown(adapter.astart(), teardown=_teardown_adapter_start)

    typing_listener = TypingListener(
        nc=nc,
        subject=typing_deps.subject,
        resolver=typing_deps.scope_resolver,
        factory_builder=make_typing_factory(typing_deps.worker_factory),
        manager=adapter._typing,
    )
    async def _teardown_typing_start() -> None:
        await close_safely(
            f"{platform_enum.value}-typing-start",
            typing_listener.stop(),
            adapter.close(),
            inbound_bus.stop(),
        )

    await run_with_teardown(typing_listener.start(), teardown=_teardown_typing_start)

    # Audio consumer: started strictly after astart() + typing, so no
    # cleanup needed in either astart or typing failure paths above.
    consumer = await start_audio_consumer(js, platform_enum.value, bot_id, adapter)

    return (adapter, inbound_bus, typing_listener, consumer)
