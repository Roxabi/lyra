"""lyra.nats — NATS transport backends for Lyra.

Provides ``NatsBus[T]`` — a concrete implementation of the ``Bus[T]`` Protocol
backed by NATS pub-sub for Hub ↔ Adapter IPC across separate OS processes.

``LocalBus`` remains the default for single-machine / dev-mode operation.
``NatsBus`` is injected via DI when Hub runs in distributed mode (Slice C of #445).

Usage::

    import nats
    from lyra.nats import NatsBus
    from lyra.core.message import InboundMessage, Platform

    nc = await nats.connect("nats://127.0.0.1:4222")
    bus: Bus[InboundMessage] = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)
    bus.register(Platform.TELEGRAM)
    await bus.start()
    ...
    await bus.stop()
"""
from .nats_bus import NatsBus

__all__ = ["NatsBus"]
