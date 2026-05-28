"""JetStreamAudioConsumer — durable JetStream pull consumer for outbound audio.

Delivers ``lyra.outbound.audio.<platform>.<bot_id>`` messages to the platform
adapter with exactly-once-effective semantics (Model A):

  - Ack ONLY after a confirmed successful platform send.
  - Transient send failure → do NOT ack → JetStream redelivers (AckWait=90s).
  - Terminal (num_delivered >= MAX_DELIVER on a failing send) → term() + user
    notification via notify_undelivered().
  - Dedup via an injected SentSet (V1: InMemorySentSet; T10: KvSentSet).

Separate from NatsOutboundListener by axial decision (#1482).
T10 swap: replace default ``InMemorySentSet()`` with ``KvSentSet(kv)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import nats.errors

from lyra.adapters.nats.jetstream_audio_dedup import InMemorySentSet, KvSentSet
from lyra.adapters.nats.jetstream_audio_envelope import (
    decode_audio_envelope,
    num_delivered,
)
from lyra.core.messaging.message import InboundMessage, OutboundAudio, OutboundMessage
from lyra.core.messaging.voice_notify import notify_undelivered
from lyra.infrastructure.outbound_audio.stream_setup import MAX_DELIVER
from roxabi_contracts.outbound import STREAM_AUDIO

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

_FETCH_BATCH = 5
_FETCH_TIMEOUT = 5.0  # seconds — keeps the loop responsive

# ---------------------------------------------------------------------------
# In-process metric counters (#1482 T11)
# These counters are per-process/per-consumer-instance; tests assert on them
# directly. The monitoring subsystem observes the same signals via the NATS
# HTTP /jsz endpoint (consumer num_pending + stream num_bytes) rather than
# reading these counters at runtime — they are purely for test observability
# and local introspection.
# ---------------------------------------------------------------------------
#: Number of times term() fired (silent-loss-averted events). Any value > 0
#: is alertable. Monotonically increasing since process start.
audio_terminal_drop_total: int = 0

#: Number of messages observed with num_delivered > 1 (redeliveries).
audio_redelivery_total: int = 0

AudioSendFn = Callable[[OutboundAudio, InboundMessage], Awaitable[None]]
TextSendFn = Callable[[InboundMessage, OutboundMessage], Awaitable[None]]


class JetStreamAudioConsumer:
    """Durable pull consumer for LYRA_OUTBOUND_AUDIO stream.

    Lifecycle::

        await ensure_stream(js)
        await ensure_consumer(js, durable=..., filter_subject=...)
        consumer = JetStreamAudioConsumer(js, durable=..., filter_subject=...,
                                          send_audio=adapter.render_audio,
                                          send_text=adapter.send)
        await consumer.start()
        # ... running ...
        await consumer.stop()

    Dedup (T10 swap target):
        The injected ``dedup`` object (default: ``InMemorySentSet()``) is the
        sole swap point for T10. Replace with ``KvSentSet(kv)`` and make the
        call-sites ``await``-aware — the loop body is otherwise unchanged.
    """

    def __init__(  # noqa: PLR0913
        self,
        js: "JetStreamContext",
        *,
        durable: str,
        filter_subject: str,
        send_audio: AudioSendFn,
        send_text: TextSendFn,
        stream_name: str = STREAM_AUDIO,
        max_deliver: int = MAX_DELIVER,
        dedup: InMemorySentSet | KvSentSet | None = None,
    ) -> None:
        self._js = js
        self._durable = durable
        self._filter_subject = filter_subject
        self._send_audio = send_audio
        self._send_text = send_text
        self._stream_name = stream_name
        self._max_deliver = max_deliver
        self._dedup = dedup if dedup is not None else InMemorySentSet()

        self._sub: Any = None  # nats pull subscription
        self._task: asyncio.Task[None] | None = None

        # Guard: fire the terminal user-notification exactly once per stream_id.
        self._notified: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind to the durable consumer and launch the background pull loop.

        Consumer config (AckWait, MaxDeliver, etc.) is owned by ensure_consumer
        in stream_setup.py — nats-py ignores config= for existing durables.
        """
        self._sub = await self._js.pull_subscribe(
            self._filter_subject,
            durable=self._durable,
            stream=self._stream_name,
        )
        self._task = asyncio.create_task(
            self._consume_loop(), name=f"js-audio-consumer:{self._durable}"
        )

        def _on_task_done(t: asyncio.Task[None]) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error(
                    "JetStreamAudioConsumer: consume loop exited unexpectedly"
                    " (consumer=%s exc_type=%s) — Quadlet will restart",
                    self._durable,
                    type(exc).__name__,
                )

        self._task.add_done_callback(_on_task_done)
        log.info(
            "JetStreamAudioConsumer started (stream=%s consumer=%s filter=%s)",
            self._stream_name,
            self._durable,
            self._filter_subject,
        )

    async def stop(self) -> None:
        """Cancel the consume loop and clean up the subscription."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._sub is not None:
            with contextlib.suppress(Exception):
                await self._sub.unsubscribe()
            self._sub = None
        log.info("JetStreamAudioConsumer stopped (consumer=%s)", self._durable)

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Pull batches, dispatch per message, ack/nak/term based on outcome."""
        while True:
            try:
                msgs = await self._sub.fetch(  # type: ignore[union-attr]
                    batch=_FETCH_BATCH, timeout=_FETCH_TIMEOUT
                )
            except nats.errors.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except nats.errors.ConnectionClosedError:
                log.error(
                    "JetStreamAudioConsumer: NATS connection lost"
                    " (consumer=%s), exiting — Quadlet will restart",
                    self._durable,
                )
                raise

            for msg in msgs:
                await self._process(msg)

    async def _process(self, msg: Any) -> None:
        """Handle a single JetStream message: decode → dedup → send → ack/term."""
        stream_id, audio, inbound = decode_audio_envelope(msg)

        if stream_id is None or audio is None or inbound is None:
            # Malformed envelope — ack so it does not block the consumer.
            log.warning(
                "JetStreamAudioConsumer: malformed envelope on subject=%s,"
                " acking to unblock",
                msg.subject,
            )
            with contextlib.suppress(Exception):
                await msg.ack()
            return

        # Dedup: already delivered → ack + skip. KV read error → treat as not-sent
        # (bounded double-send risk preferable to crashed consumer loop).
        try:
            already = await self._dedup.already_sent(stream_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "JetStreamAudioConsumer: dedup read error stream_id=%r"
                " (exc_type=%s), treating as not-sent",
                stream_id,
                type(exc).__name__,
            )
            already = False
        if already:
            log.debug(
                "JetStreamAudioConsumer: dedup hit for stream_id=%r, acking",
                stream_id,
            )
            with contextlib.suppress(Exception):
                await msg.ack()
            return

        n_delivered = num_delivered(msg)
        if n_delivered > 1:  # redelivery observation (#1482 T11)
            global audio_redelivery_total
            audio_redelivery_total += 1

        try:
            await self._send_audio(audio, inbound)
        except Exception:
            log.exception(
                "JetStreamAudioConsumer: send_audio failed for"
                " stream_id=%r (delivered=%d/%d)",
                stream_id,
                n_delivered,
                self._max_deliver,
            )
            if n_delivered >= self._max_deliver:
                await self._handle_terminal(msg, stream_id, inbound)
            # Transient: do NOT ack — JetStream will redeliver after AckWait.
            return

        # Success: record send, then ack.
        await self._dedup.mark_sent(stream_id)
        try:
            await msg.ack()
        except Exception:
            log.exception(
                "JetStreamAudioConsumer: ack failed for stream_id=%r"
                " — message may redeliver but dedup will guard",
                stream_id,
            )

    async def _handle_terminal(
        self, msg: Any, stream_id: str, inbound: InboundMessage
    ) -> None:
        """Terminate message + notify user exactly once per stream_id.

        Notification is sent ONLY after a successful term() — if term() fails,
        JetStream redelivers and we retry rather than notify prematurely.
        """
        global audio_terminal_drop_total
        # increment before term() in case it raises (#1482 T11)
        audio_terminal_drop_total += 1
        termed = False
        try:
            await msg.term()
            termed = True
            log.warning(
                "JetStreamAudioConsumer: terminal for stream_id=%r"
                " — termed, notifying user",
                stream_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "JetStreamAudioConsumer: term() failed for stream_id=%r"
                " (exc_type=%s) — will retry on next redelivery",
                stream_id,
                type(exc).__name__,
            )

        if not termed:
            return

        # _notified: bounded by terminal-failure count per lifetime — negligible.
        if stream_id in self._notified:
            return
        self._notified.add(stream_id)

        outbound = notify_undelivered(context="audio-terminal-undelivered")
        try:
            await self._send_text(inbound, outbound)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "JetStreamAudioConsumer: user notification failed"
                " for stream_id=%r (exc_type=%s, best-effort)",
                stream_id,
                type(exc).__name__,
            )
