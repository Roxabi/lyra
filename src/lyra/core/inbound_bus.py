"""LocalBus — generic per-platform inbound queues with feeder tasks and staging queue.

Concrete implementation of the ``Bus[T]`` Protocol defined in ``bus.py``.
Each registered platform gets its own bounded asyncio.Queue. Independent feeder
tasks drain per-platform queues into a single staging queue consumed by Hub.run().
This isolates platform-level backpressure: a flood on one platform cannot starve
another platform's messages.

Usage::

    from lyra.core.bus import Bus
    from lyra.core.inbound_bus import LocalBus
    from lyra.core.message import InboundMessage, InboundAudio, Platform

    # Text messages
    bus: Bus[InboundMessage] = LocalBus(name="inbound")
    bus.register(Platform.TELEGRAM, maxsize=100)
    await bus.start()
    ...
    await bus.stop()

    # Audio envelopes — depth monitoring included
    audio_bus: Bus[InboundAudio] = LocalBus(name="inbound-audio")
    audio_bus.register(Platform.TELEGRAM, maxsize=100)
    await audio_bus.start()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Generic, TypeVar

from .message import Platform

log = logging.getLogger(__name__)

T = TypeVar("T")


class LocalBus(Generic[T]):
    """Generic per-platform inbound queues + staging queue consumed by Hub.run().

    Concrete implementation of ``Bus[T]`` Protocol.

    Lifecycle::

        bus: Bus[InboundMessage] = LocalBus(name="inbound")
        bus.register(Platform.TELEGRAM, maxsize=100)
        bus.register(Platform.DISCORD, maxsize=100)
        await bus.start()          # spawns feeder tasks
        ...
        await bus.stop()           # cancels feeder tasks

    Args:
        name: Prefix used for feeder task names and log messages.
            Use ``"inbound"`` for text messages and ``"inbound-audio"`` for audio.
        queue_depth_threshold: Staging queue depth above which a warning is
            logged (edge-triggered).
    """

    def __init__(
        self,
        name: str = "inbound",
        queue_depth_threshold: int = 100,
        staging_maxsize: int = 500,
    ) -> None:
        self._name = name
        self._queues: dict[Platform, asyncio.Queue[T]] = {}
        self._staging: asyncio.Queue[T] = asyncio.Queue(maxsize=staging_maxsize)
        self._feeders: dict[Platform, asyncio.Task[None]] = {}
        self._threshold = queue_depth_threshold
        self._depth_exceeded = False

    def register(  # noqa: ARG002
        self, platform: Platform, maxsize: int = 100, bot_id: str | None = None
    ) -> None:
        """Register a bounded queue for the given platform.

        Must be called before start(). Raises RuntimeError if called after
        start() — the new queue would have no feeder task and messages would
        silently never reach staging.

        bot_id is accepted for protocol compatibility but ignored by LocalBus.
        """
        if self._feeders:
            raise RuntimeError(
                f"Cannot register platform {platform!r} after start() — "
                "feeders are already running."
            )
        if platform in self._queues:
            return  # Already registered — idempotent for multi-bot
        self._queues[platform] = asyncio.Queue(maxsize=maxsize)

    async def put(self, platform: Platform, item: T) -> None:
        """Enqueue an item on the platform's queue.

        Raises asyncio.QueueFull if the platform queue is at capacity.
        The caller (adapter) is responsible for sending a backpressure ack
        and dropping the item on QueueFull.
        """
        self._queues[platform].put_nowait(item)

    async def get(self) -> T:
        """Wait for and return the next item from the staging queue.

        Called exclusively by Hub.run() or AudioPipeline.run() (single consumer).
        """
        return await self._staging.get()

    def task_done(self) -> None:
        """Notify staging queue that the current item was processed."""
        self._staging.task_done()

    async def start(self) -> None:
        """Spawn one feeder task per registered platform.

        Raises RuntimeError if called while feeders are already running.
        """
        if self._feeders:
            raise RuntimeError(
                f"LocalBus({self._name!r}).start() called while feeders are "
                "already running — call stop() first."
            )
        for platform, queue in self._queues.items():
            task = asyncio.create_task(
                self._feeder(platform, queue),
                name=f"{self._name}-feeder-{platform.value}",
            )
            self._feeders[platform] = task

    async def _feeder(self, platform: Platform, queue: asyncio.Queue[T]) -> None:
        """Drain platform queue into the staging queue indefinitely."""
        log.debug(
            "LocalBus(%r) feeder started for platform=%s",
            self._name,
            platform.value,
        )
        while True:
            item = await queue.get()
            await self._staging.put(item)
            queue.task_done()
            depth = self._staging.qsize()
            if depth > self._threshold and not self._depth_exceeded:
                self._depth_exceeded = True
                log.warning(
                    "queue depth exceeded: queue=%s-staging depth=%d threshold=%d",
                    self._name,
                    depth,
                    self._threshold,
                )
            elif depth <= self._threshold and self._depth_exceeded:
                self._depth_exceeded = False
                log.info(
                    "queue depth normal: queue=%s-staging depth=%d",
                    self._name,
                    depth,
                )

    async def stop(self) -> None:
        """Cancel all feeder tasks and wait for them to finish."""
        for task in self._feeders.values():
            task.cancel()
        if self._feeders:
            await asyncio.gather(*self._feeders.values(), return_exceptions=True)
        self._feeders.clear()

    def qsize(self, platform: Platform) -> int:
        """Return the current number of items in the platform's queue."""
        queue = self._queues.get(platform)
        return queue.qsize() if queue is not None else 0

    def staging_qsize(self) -> int:
        """Return the current number of items in the staging queue."""
        return self._staging.qsize()

    def inject(self, item: T) -> None:
        """Public injection API for compat shims.

        Enqueues an item directly into the staging queue, bypassing any
        publisher-side tracing or metrics. Used by InboundAudioLegacyHandler
        in Slice 1 of issue #534 to feed legacy-subject voice messages into
        the unified inbound path. Callers must not rely on item order relative
        to concurrent ``put()`` callers — inject is a separate entry point.
        """
        self._staging.put_nowait(item)

    def registered_platforms(self) -> frozenset[Platform]:
        """Return the set of platforms with a registered queue."""
        return frozenset(self._queues)

    @property
    def subscription_count(self) -> int:
        """LocalBus has no remote subscriptions — always 0."""
        return 0
