"""TurnPublisher — publish TurnWriteEvent to lyra.turns.write awaiting JetStream PubAck.

Awaiting PubAck guarantees the event is persisted in the stream before the
inbound message is acked.  End-to-end delivery is at-least-once via JetStream.

``trace_id`` is a required field on ``ContractEnvelope`` (min_length=1 constraint).
Callers MUST supply a non-empty trace_id that propagates the originating request
context.  Slice 3 call-sites (pool_observer, session lifecycle hooks) are
responsible for threading a sensible trace_id (e.g. a UUID generated at the top
of the inbound message handler) into every publish call.

Usage::

    js = nc.jetstream()
    publisher = TurnPublisher(js)
    await publisher.publish_log_turn(
        pool_id="...", session_id="...", platform="telegram",
        user_id="...", role="user", content="hello",
        message_id="msg_123", trace_id="<uuid>",
    )
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.turns import (
    SUBJECTS,
    EndSessionPayload,
    IncrementResumeCountPayload,
    LogTurnPayload,
    SetCliSessionPayload,
    StartSessionPayload,
    TurnWriteEvent,
)

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


class TurnPublisher:
    """Publish TurnWriteEvent to lyra.turns.write, awaiting JetStream PubAck.

    ``js`` must be a ``nats.js.client.JetStreamContext`` obtained via
    ``nc.jetstream()``.  ``JetStreamContext.publish()`` awaits the server
    PubAck before returning, which is the persistence guarantee required
    by the turn-store alpha.
    """

    def __init__(self, js: "JetStreamContext") -> None:
        self._js = js

    async def _publish(self, event: TurnWriteEvent) -> None:
        payload = event.model_dump_json().encode("utf-8")
        await self._js.publish(SUBJECTS.turn_write, payload)
        log.debug(
            "turn_publisher: published kind=%s session=%s",
            event.kind,
            event.session_id,
        )

    async def publish_log_turn(  # noqa: PLR0913
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        role: str,
        content: str,
        message_id: str | None = None,
        reply_message_id: str | None = None,
        metadata: dict | None = None,
        trace_id: str,
    ) -> None:
        """Publish a log_turn event (user or assistant message content)."""
        now = datetime.now(UTC)
        await self._publish(
            TurnWriteEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=now,
                pool_id=pool_id,
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                timestamp=now,
                payload=LogTurnPayload(
                    role=role,  # type: ignore[arg-type]  # Pydantic narrows Literal at validation
                    content=content,
                    message_id=message_id,
                    reply_message_id=reply_message_id,
                    metadata=metadata or {},
                ),
            )
        )

    async def publish_start_session(
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        trace_id: str,
    ) -> None:
        """Publish a start_session event."""
        now = datetime.now(UTC)
        await self._publish(
            TurnWriteEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=now,
                pool_id=pool_id,
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                timestamp=now,
                payload=StartSessionPayload(),
            )
        )

    async def publish_end_session(
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        trace_id: str,
    ) -> None:
        """Publish an end_session event."""
        now = datetime.now(UTC)
        await self._publish(
            TurnWriteEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=now,
                pool_id=pool_id,
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                timestamp=now,
                payload=EndSessionPayload(),
            )
        )

    async def publish_set_cli_session(  # noqa: PLR0913
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        cli_session_id: str,
        trace_id: str,
    ) -> None:
        """Publish a set_cli_session event."""
        now = datetime.now(UTC)
        await self._publish(
            TurnWriteEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=now,
                pool_id=pool_id,
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                timestamp=now,
                payload=SetCliSessionPayload(cli_session_id=cli_session_id),
            )
        )

    async def publish_increment_resume_count(  # noqa: PLR0913
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        target_count: int,
        trace_id: str,
    ) -> None:
        """Publish an increment_resume_count event."""
        now = datetime.now(UTC)
        await self._publish(
            TurnWriteEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=trace_id,
                issued_at=now,
                pool_id=pool_id,
                session_id=session_id,
                platform=platform,
                user_id=user_id,
                timestamp=now,
                payload=IncrementResumeCountPayload(target_count=target_count),
            )
        )
