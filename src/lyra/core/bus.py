"""Bus — generic inbound message transport protocol.

Defines the ``Bus[T]`` Protocol that the Hub depends on for inbound message
routing.  The concrete implementation is ``LocalBus`` in ``inbound_bus.py``
(backed by ``asyncio.Queue``).  Future transports (e.g. NATS, issue #50)
implement the same Protocol without touching Hub internals.

Usage::

    from lyra.core.bus import Bus
    from lyra.core.inbound_bus import LocalBus
    from lyra.core.message import InboundMessage

    bus: Bus[InboundMessage] = LocalBus(name="inbound")
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from .message import Platform

T = TypeVar("T")


class Bus(Protocol[T]):
    """Generic inbound message transport.

    Structural protocol — concrete classes satisfy it without inheriting.
    Invariant in ``T`` (both covariant ``get() -> T`` and contravariant
    ``put(item: T)`` positions).
    """

    def register(self, platform: Platform, maxsize: int = 100) -> None:
        """Register a bounded queue for the given platform."""
        ...

    def put(self, platform: Platform, item: T) -> None:
        """Enqueue an item on the platform's queue (synchronous, non-blocking)."""
        ...

    async def get(self) -> T:
        """Wait for and return the next item from the staging queue."""
        ...

    def task_done(self) -> None:
        """Notify that the current item was processed."""
        ...

    async def start(self) -> None:
        """Start the bus (spawn feeder tasks, open connections, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop the bus and clean up resources."""
        ...

    def qsize(self, platform: Platform) -> int:
        """Return the current number of items in the platform's queue."""
        ...

    def staging_qsize(self) -> int:
        """Return the current number of items in the staging queue."""
        ...

    def registered_platforms(self) -> frozenset[Platform]:
        """Return the set of platforms with a registered queue."""
        ...
