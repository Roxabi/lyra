"""NatsBus — Bus[T] implementation over NATS pub/sub.

Concrete implementation of the ``Bus[T]`` Protocol defined in ``bus.py``,
using NATS as the transport layer instead of local asyncio queues.

Each registered (platform, bot_id) pair maps to one NATS subscription on the
subject ``lyra.inbound.{platform.value}.{bot_id}``.  Inbound messages are
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

Multi-bot usage::

    bus.register(Platform.TELEGRAM, bot_id="bot-a")
    bus.register(Platform.TELEGRAM, bot_id="bot-b")
    await bus.start()  # two subscriptions: one per (platform, bot_id) pair
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, Generic, TypeVar

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from lyra.core.message import Platform
from lyra.nats._sanitize import sanitize_platform_meta
from lyra.nats._serialize import deserialize, serialize
from lyra.nats._validate import validate_nats_token

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
        await bus.stop()    # unsubscribes; registrations remain intact
        await bus.start()   # safe to restart without re-registering

    Args:
        nc: Already-connected ``nats.NATS`` client.
        bot_id: Default bot identifier used when ``register()`` is called
            without an explicit ``bot_id``.
        item_type: Concrete type used for deserialization (e.g. ``InboundMessage``).
        subject_prefix: NATS subject prefix. Defaults to ``"lyra.inbound"``.
            Use a different prefix (e.g. ``"lyra.inbound.audio"``) to avoid
            subject collisions between different message types.
    """

    def __init__(  # noqa: PLR0913
        self,
        nc: NATS,
        bot_id: str,
        item_type: type[T],
        subject_prefix: str = "lyra.inbound",
        *,
        staging_maxsize: int = 500,
        queue_group: str = "",
    ) -> None:
        validate_nats_token(subject_prefix, kind="subject_prefix")
        validate_nats_token(queue_group, kind="queue_group", allow_empty=True)
        self._nc = nc
        self._bot_id = bot_id
        self._item_type = item_type
        self._subject_prefix = subject_prefix
        self._queue_group = queue_group
        self._registrations: set[tuple[Platform, str]] = set()
        self._subscriptions: dict[tuple[Platform, str], Subscription] = {}
        self._staging: asyncio.Queue[T] = asyncio.Queue(maxsize=staging_maxsize)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(  # noqa: ARG002
        self, platform: Platform, maxsize: int = 100, bot_id: str | None = None
    ) -> None:
        """Record *(platform, bot_id)* for subscription setup.

        ``maxsize`` is accepted for Protocol compatibility but unused — there
        is no per-platform local buffer in NatsBus.

        When ``bot_id`` is omitted or ``None``, the constructor's ``bot_id``
        is used, preserving backward compatibility.

        Raises:
            RuntimeError: If called after ``start()``.
        """
        if self._subscriptions:
            raise RuntimeError(
                f"Cannot register platform {platform!r} after start() — "
                "subscriptions are already active."
            )
        resolved_bid = bot_id or self._bot_id
        validate_nats_token(resolved_bid, kind="bot_id")
        if (platform, resolved_bid) in self._registrations:
            return  # Already registered — idempotent
        self._registrations.add((platform, resolved_bid))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create one NATS subscription per registered (platform, bot_id) pair.

        Subject pattern: ``{subject_prefix}.{platform.value}.{bot_id}``

        No-op if zero registrations exist.

        Raises:
            RuntimeError: If subscriptions are already active (double-start).
        """
        if self._subscriptions:
            raise RuntimeError(
                "NatsBus.start() called while subscriptions are already active — "
                "call stop() first."
            )
        for platform, bid in self._registrations:
            await self._make_handler(platform, bid)

    async def stop(self) -> None:
        """Unsubscribe all active NATS subscriptions.

        Registered (platform, bot_id) pairs are preserved so that a subsequent
        ``start()`` succeeds without re-registering.
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

        Uses the first matching registration's bot_id for the subject.

        Raises:
            KeyError: If *platform* has not been registered.
        """
        registered_platforms = frozenset(p for p, _ in self._registrations)
        if platform not in registered_platforms:
            raise KeyError(
                f"Platform {platform!r} is not registered — call register() first."
            )
        bid = next((b for p, b in self._registrations if p == platform), self._bot_id)
        subject = f"{self._subject_prefix}.{platform.value}.{bid}"
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
        return frozenset(p for p, _ in self._registrations)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _make_handler(self, platform: Platform, bot_id: str) -> None:
        """Create NATS subscription for *(platform, bot_id)* and register the handler.
        """
        subject = f"{self._subject_prefix}.{platform.value}.{bot_id}"

        async def handler(msg: Msg) -> None:
            try:
                item = deserialize(msg.data, self._item_type)
                if hasattr(item, "platform_meta"):
                    _item: Any = item
                    item = dataclasses.replace(
                        _item,
                        platform_meta=sanitize_platform_meta(_item.platform_meta),
                    )
                self._staging.put_nowait(item)
            except asyncio.QueueFull:
                log.warning(
                    "NatsBus staging queue full — dropping message on"
                    " platform=%s bot_id=%s",
                    platform.value,
                    bot_id,
                )
            except Exception:
                log.exception(
                    "NatsBus: failed to deserialize message on platform=%s bot_id=%s",
                    platform.value,
                    bot_id,
                )

        sub = await self._nc.subscribe(subject, queue=self._queue_group, cb=handler)
        self._subscriptions[(platform, bot_id)] = sub
        log.debug(
            "NatsBus subscribed: subject=%s bot_id=%s queue_group=%r",
            subject,
            bot_id,
            self._queue_group,
        )
