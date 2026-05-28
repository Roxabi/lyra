"""ResumePublisherPort — driven port for turn resume publishing.

Exposes both the publish-side (increment resume count) and the read-side
(get current resume count) so that MessagePrepMiddleware can wire the pool
resume callback without drilling into ctx.hub concrete internals.

Pure Protocol — no infrastructure imports (TYPE_CHECKING-only permitted).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ResumePublisherPort(Protocol):
    async def publish_increment_resume_count(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps# mirrors TurnPublisher signature
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        target_count: int,
        trace_id: str,
    ) -> None: ...

    async def get_resume_count(self, session_id: str) -> int: ...
