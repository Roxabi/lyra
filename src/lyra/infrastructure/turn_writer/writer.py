"""TurnWriter — JetStream subscriber that persists turn events to SQLite.

Consumes `lyra.turns.write` and dispatches per-kind handlers. Each handler
implements its own idempotence strategy (UNIQUE constraint catch, INSERT OR
IGNORE, high-water mark + processed_events catch).

ADR-075 authorises this sublayer.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import nats.errors

from lyra.infrastructure.stores.turn_store import TurnStore
from roxabi_contracts.turns import (
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

_FETCH_BATCH = 10
_FETCH_TIMEOUT = 5.0  # seconds — short to keep the loop responsive


class TurnWriter:
    """Subscribe to lyra.turns.write, persist via TurnStore mutators.

    The writer runs as a long-lived task. `start()` subscribes and spawns the
    consume loop; `stop()` cancels and drains. Call `ensure_stream` and
    `ensure_consumer` (from stream_setup) before `start()`.

    Per-event flow:
      1. Deserialise TurnWriteEvent from msg.data.
      2. Dispatch by payload.kind to handler.
      3. Handler writes via TurnStore private API; on success, ack.
      4. On exception, log + nak; JetStream redelivers (AckWait=60s).
    """

    def __init__(self, turn_store: TurnStore, js: "JetStreamContext") -> None:
        self._store = turn_store
        self._js = js
        self._task: asyncio.Task[None] | None = None
        self._sub: "JetStreamContext.PullSubscription | None" = None
        # Oldest-pending timestamp for lag gauge (S9).
        self._oldest_pending: datetime | None = None

    async def start(self) -> None:
        """Subscribe to LYRA_TURNS/turn-writer-v1 and begin processing.

        Requires ensure_stream() + ensure_consumer() to have been called first
        so the durable consumer exists before we bind.
        """
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

        from lyra.infrastructure.turn_writer.stream_setup import (
            CONSUMER_NAME,
            STREAM_NAME,
            SUBJECT_FILTER,
        )

        config = ConsumerConfig(
            durable_name=CONSUMER_NAME,
            name=CONSUMER_NAME,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=60.0,
            max_deliver=5,
            filter_subject=SUBJECT_FILTER,
        )
        self._sub = await self._js.pull_subscribe(
            SUBJECT_FILTER,
            durable=CONSUMER_NAME,
            stream=STREAM_NAME,
            config=config,
        )
        self._task = asyncio.create_task(self._consume_loop(), name="turn-writer")
        log.info(
            "TurnWriter started (stream=%s consumer=%s)", STREAM_NAME, CONSUMER_NAME
        )

    async def stop(self) -> None:
        """Cancel the consume task and drain in-flight messages."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("TurnWriter stopped")

    @property
    def oldest_pending(self) -> datetime | None:
        """Oldest in-flight message receipt time for lag gauge (S9)."""
        return self._oldest_pending

    async def _consume_loop(self) -> None:
        """Pull batches, dispatch per message, ack on success / nak on error."""
        while True:
            try:
                msgs = await self._sub.fetch(  # type: ignore[union-attr]
                    batch=_FETCH_BATCH, timeout=_FETCH_TIMEOUT
                )
            except nats.errors.TimeoutError:
                # No messages available — keep looping.
                continue
            except asyncio.CancelledError:
                return

            for msg in msgs:
                received_at = datetime.now(UTC)
                if self._oldest_pending is None:
                    self._oldest_pending = received_at

                try:
                    event = TurnWriteEvent.model_validate_json(msg.data)
                    await self._handle(event)
                    await msg.ack()
                    self._oldest_pending = None
                except Exception:
                    log.exception(
                        "turn-writer: handler failed for msg subject=%s — naking",
                        msg.subject,
                    )
                    try:
                        await msg.nak()
                    except Exception:
                        log.exception("turn-writer: nak failed")
                    self._oldest_pending = None

    async def _handle(self, event: TurnWriteEvent) -> None:
        payload = event.payload
        if isinstance(payload, LogTurnPayload):
            await self._handle_log_turn(event, payload)
        elif isinstance(payload, StartSessionPayload):
            await self._handle_start_session(event, payload)
        elif isinstance(payload, EndSessionPayload):
            await self._handle_end_session(event, payload)
        elif isinstance(payload, SetCliSessionPayload):
            await self._handle_set_cli_session(event, payload)
        else:
            # Narrowed to IncrementResumeCountPayload by union exhaustion.
            await self._handle_increment_resume_count(event, payload)

    async def _handle_log_turn(
        self, event: TurnWriteEvent, p: LogTurnPayload
    ) -> None:
        # _log_turn uses bare INSERT; UNIQUE(platform, message_id) raises
        # IntegrityError on duplicate — treat as idempotent no-op.
        try:
            await self._store._log_turn(
                pool_id=event.pool_id,
                session_id=event.session_id,
                role=p.role,
                platform=event.platform,
                user_id=event.user_id,
                content=p.content,
                message_id=p.message_id,
                reply_message_id=p.reply_message_id,
                metadata=p.metadata,
            )
        except sqlite3.IntegrityError:
            log.debug(
                "turn-writer: duplicate (platform=%s message_id=%s) — skipped",
                event.platform,
                p.message_id,
            )

    async def _handle_start_session(
        self, event: TurnWriteEvent, p: StartSessionPayload
    ) -> None:
        # INSERT OR IGNORE — naturally idempotent.
        await self._store._start_session(event.session_id, event.pool_id)

    async def _handle_end_session(
        self, event: TurnWriteEvent, p: EndSessionPayload
    ) -> None:
        # UPDATE WHERE ended_at IS NULL — naturally idempotent.
        await self._store._end_session(event.session_id)

    async def _handle_set_cli_session(
        self, event: TurnWriteEvent, p: SetCliSessionPayload
    ) -> None:
        # UPDATE — idempotent (same value on replay).
        await self._store._set_cli_session(event.session_id, p.cli_session_id)

    async def _handle_increment_resume_count(
        self, event: TurnWriteEvent, p: IncrementResumeCountPayload
    ) -> None:
        # High-water mark + processed_events catch.
        # Bypasses _increment_resume_count (which does +1) in favour of
        # max(current, target_count) to guarantee idempotence on replay.
        db = self._store._db_or_raise()
        async with db.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?",
                (str(event.event_id),),
            )
            if await cur.fetchone() is not None:
                log.debug(
                    "turn-writer: event_id=%s already processed — skipped",
                    event.event_id,
                )
                return
            await cur.execute(
                "UPDATE pool_sessions"
                " SET resume_count = max(resume_count, ?)"
                " WHERE session_id = ?",
                (p.target_count, event.session_id),
            )
            await cur.execute(
                "INSERT INTO processed_events(event_id, processed_at) VALUES (?, ?)",
                (str(event.event_id), datetime.now(UTC).isoformat()),
            )
        await db.commit()
