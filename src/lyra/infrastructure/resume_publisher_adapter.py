"""TurnPublisherAdapter — infrastructure adapter for ResumePublisherPort.

Wraps TurnPublisher (NATS publish) + TurnStore (SQLite read) to satisfy the
domain port defined in lyra.core.ports.resume_publisher.
"""

from __future__ import annotations

from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.transport.turn_publisher import TurnPublisher


class TurnPublisherAdapter:
    """Adapter implementing ResumePublisherPort.

    Delegates ``publish_increment_resume_count`` to the NATS-backed
    ``TurnPublisher`` and ``get_resume_count`` to the SQLite-backed
    ``TurnStore``.
    """

    def __init__(self, publisher: TurnPublisher, store: TurnStore) -> None:
        self._publisher = publisher
        self._store = store

    async def publish_increment_resume_count(self, **kwargs) -> None:
        await self._publisher.publish_increment_resume_count(**kwargs)

    async def get_resume_count(self, session_id: str) -> int:
        return await self._store.get_resume_count(session_id)
