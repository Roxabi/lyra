"""NatsBus — Bus[T] implementation over NATS pub/sub.

Concrete implementation of the ``Bus[T]`` Protocol defined in ``bus.py``,
using NATS as the transport layer instead of local asyncio queues.

Each registered platform maps to one NATS subscription on the subject
``lyra.inbound.{platform.value}.{bot_id}``.  Inbound messages are
deserialized from JSON and placed into a single staging queue consumed
by Hub.run().

Usage::

    import nats
    from lyra.nats.nats_bus import NatsBus
    from lyra.core.message import InboundMessage, Platform

    nc = await nats.connect("nats://localhost:4222")
    bus: Bus[InboundMessage] = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)
    bus.register(Platform.TELEGRAM)
    await bus.start()
    ...
    await bus.stop()
    await nc.close()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, TypeVar

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from lyra.core.message import Platform
from lyra.nats._serialize import deserialize, serialize

log = logging.getLogger(__name__)

T = TypeVar("T")


class NatsBus(Generic[T]):
    """Bus[T] implementation backed by NATS pub/sub.

    The caller is responsible for establishing and closing the NATS connection.
    ``NatsBus`` only manages subscriptions — it never calls ``nc.connect()``
    or ``nc.close()``.

    Lifecycle::

        nc = await nats.connect("nats://localhost:4222")
        bus = NatsBus(nc=nc, bot_id="main", item_type=InboundMessage)
        bus.register(Platform.TELEGRAM)
        await bus.start()   # creates NATS subscriptions
        ...
        await bus.stop()    # unsubscribes; platforms remain registered
        await bus.start()   # safe to restart without re-registering

    Args:
        nc: Already-connected ``nats.NATS`` client.
        bot_id: Bot identifier appended to the NATS subject.
        item_type: Concrete type used for deserialization (e.g. ``InboundMessage``).
    """

    def __init__(self, nc: NATS, bot_id: str, item_type: type[T]) -> None:
        self._nc = nc
        self._bot_id = bot_id
        self._item_type = item_type
        self._platforms: set[Platform] = set()
        self._subscriptions: dict[Platform, Subscription] = {}
        self._staging: asyncio.Queue[T] = asyncio.Queue(maxsize=500)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, platform: Platform, maxsize: int = 100) -> None:  # noqa: ARG002
        """Record *platform* for subscription setup.

        ``maxsize`` is accepted for Protocol compatibility but unused — there
        is no per-platform local buffer in NatsBus.

        Raises:
            RuntimeError: If called after ``start()``.
        """
        if self._subscriptions:
            raise RuntimeError(
                f"Cannot register platform {platform!r} after start() — "
                "subscriptions are already active."
            )
        self._platforms.add(platform)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create one NATS subscription per registered platform.

        Subject pattern: ``lyra.inbound.{platform.value}.{bot_id}``

        No-op if zero platforms are registered.

        Raises:
            RuntimeError: If subscriptions are already active (double-start).
        """
        if self._subscriptions:
            raise RuntimeError(
                "NatsBus.start() called while subscriptions are already active — "
                "call stop() first."
            )
        for platform in self._platforms:
            await self._make_handler(platform)

    async def stop(self) -> None:
        """Unsubscribe all active NATS subscriptions.

        Registered platforms are preserved so that a subsequent ``start()``
        succeeds without re-registering.
        """
        for sub in self._subscriptions.values():
            try:
                await sub.unsubscribe()
            except Exception:
                log.exception("NatsBus: error unsubscribing")
        self._subscriptions.clear()

    # ------------------------------------------------------------------
    # Message I/O
    # ------------------------------------------------------------------

    async def put(self, platform: Platform, item: T) -> None:
        """Serialize *item* and publish it to the platform's NATS subject.

        Raises:
            KeyError: If *platform* has not been registered.
        """
        if platform not in self._platforms:
            raise KeyError(
                f"Platform {platform!r} is not registered — call register() first."
            )
        subject = f"lyra.inbound.{platform.value}.{self._bot_id}"
        payload = serialize(item)
        await self._nc.publish(subject, payload)

    async def get(self) -> T:
        """Wait for and return the next item from the staging queue."""
        return await self._staging.get()

    def task_done(self) -> None:
        """No-op — NatsBus does not use ``asyncio.Queue.task_done()`` semantics."""
        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def qsize(self, platform: Platform) -> int:  # noqa: ARG002
        """Always returns 0 — NatsBus has no per-platform local buffer."""
        return 0

    def staging_qsize(self) -> int:
        """Return the number of items currently waiting in the staging queue."""
        return self._staging.qsize()

    def registered_platforms(self) -> frozenset[Platform]:
        """Return the set of currently registered platforms."""
        return frozenset(self._platforms)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _make_handler(
        self, platform: Platform
    ) -> None:
        """Create NATS subscription for *platform* and register the handler."""
        subject = f"lyra.inbound.{platform.value}.{self._bot_id}"

        async def handler(msg: Msg) -> None:
            try:
                item = deserialize(msg.data, self._item_type)
                self._staging.put_nowait(item)
            except asyncio.QueueFull:
                log.warning(
                    "NatsBus staging queue full — dropping message on platform=%s",
                    platform.value,
                )
            except Exception:
                log.exception(
                    "NatsBus: failed to deserialize message on platform=%s",
                    platform.value,
                )

        sub = await self._nc.subscribe(subject, cb=handler)
        self._subscriptions[platform] = sub
        log.debug("NatsBus subscribed: subject=%s bot_id=%s", subject, self._bot_id)
