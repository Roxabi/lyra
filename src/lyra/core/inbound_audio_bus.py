"""InboundAudioBus — per-platform inbound audio queues.

Mirrors InboundBus but typed for InboundAudio envelopes. Each registered
platform gets its own bounded asyncio.Queue. Independent feeder tasks drain
per-platform queues into a single staging queue consumed by Hub.
"""

from __future__ import annotations

import asyncio
import logging

from .message import InboundAudio, Platform

log = logging.getLogger(__name__)


class InboundAudioBus:
    """Per-platform inbound audio queues + staging queue.

    Lifecycle:
        bus = InboundAudioBus()
        bus.register(Platform.TELEGRAM, maxsize=100)
        await bus.start()          # spawns feeder tasks
        ...
        await bus.stop()           # cancels feeder tasks
    """

    def __init__(self) -> None:
        self._queues: dict[Platform, asyncio.Queue[InboundAudio]] = {}
        self._staging: asyncio.Queue[InboundAudio] = asyncio.Queue()
        self._feeders: dict[Platform, asyncio.Task[None]] = {}

    def register(self, platform: Platform, maxsize: int = 100) -> None:
        """Register a bounded queue for the given platform.

        Must be called before start(). Raises RuntimeError if called after
        start() — the new queue would have no feeder task.
        """
        if self._feeders:
            raise RuntimeError(
                f"Cannot register platform {platform!r} after start() — "
                "feeders are already running."
            )
        self._queues[platform] = asyncio.Queue(maxsize=maxsize)

    def put(self, platform: Platform, audio: InboundAudio) -> None:
        """Enqueue an audio envelope on the platform's queue.

        Raises asyncio.QueueFull if the platform queue is at capacity.
        """
        self._queues[platform].put_nowait(audio)

    async def get(self) -> InboundAudio:
        """Wait for and return the next audio envelope from the staging queue."""
        return await self._staging.get()

    def task_done(self) -> None:
        """Notify staging queue that the current item was processed."""
        self._staging.task_done()

    async def start(self) -> None:
        """Spawn one feeder task per registered platform."""
        for platform, queue in self._queues.items():
            task = asyncio.create_task(
                self._feeder(platform, queue),
                name=f"inbound-audio-feeder-{platform.value}",
            )
            self._feeders[platform] = task

    async def _feeder(
        self, platform: Platform, queue: asyncio.Queue[InboundAudio]
    ) -> None:
        """Drain platform queue into the staging queue indefinitely."""
        log.debug("InboundAudioBus feeder started for platform=%s", platform.value)
        while True:
            audio = await queue.get()
            await self._staging.put(audio)
            queue.task_done()

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

    def registered_platforms(self) -> frozenset[Platform]:
        """Return the set of platforms with a registered queue."""
        return frozenset(self._queues)
