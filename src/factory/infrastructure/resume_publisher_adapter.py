"""TurnPublisherAdapter — infrastructure adapter for ResumePublisherPort.

Wraps TurnPublisher (NATS publish) + TurnStore (SQLite read) to satisfy the
domain port defined in factory.core.ports.resume_publisher.
"""

from __future__ import annotations

from factory.infrastructure.stores.turn_store import TurnStore
from factory.transport.turn_publisher import TurnPublisher


class TurnPublisherAdapter:
    """Adapter implementing ``ResumePublisherPort`` (structural subtyping).

    Delegates ``publish_increment_resume_count`` to the NATS-backed
    ``TurnPublisher`` and ``get_resume_count`` to the SQLite-backed
    ``TurnStore``.
    """

    def __init__(self, publisher: TurnPublisher, store: TurnStore) -> None:
        self._publisher = publisher
        self._store = store

    async def publish_increment_resume_count(  # noqa: PLR0913 — mirrors ResumePublisherPort signature
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        target_count: int,
        trace_id: str,
    ) -> None:
        await self._publisher.publish_increment_resume_count(
            pool_id=pool_id,
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            target_count=target_count,
            trace_id=trace_id,
        )

    async def get_resume_count(self, session_id: str) -> int:
        return await self._store.get_resume_count(session_id)
