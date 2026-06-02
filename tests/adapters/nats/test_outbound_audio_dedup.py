"""Slice-2 OUTCOME tests for outbound audio dedup (#1482, T12).

Acceptance criteria:

  SC6  Cross-restart dedup via shared KV — CONSUMER outcome level.
       Full scenario through JetStreamAudioConsumer._process:
         - Instance 1 sends successfully; ack() is lost (raises).
         - Instance 2 (new object, fresh in-memory state) receives the same
           stream_id via the SAME shared KV → send_audio NOT called a second
           time → redelivery acked.
       Total send_audio calls across both instances == 1.
       Negative contrast: InMemorySentSet does NOT dedup across instances
       (second instance WOULD re-send) — explicit negative test shows the
       value of persistent KV.

  SC9  Terminal metric increments — absolute-value independent.
       Drive _process to the terminal path (num_delivered >= max_deliver,
       failing send_audio).  Assert audio_terminal_drop_total delta == 1
       within the same test (before/after pattern → safe under -n auto).
       Also assert audio_redelivery_total delta == 1 on a redelivery
       (num_delivered > 1, still failing) using the same pattern.

Fakes: reuse FakeKv from test_outbound_audio_kv_dedup — inlined here to
avoid cross-module import (tests are co-located, not a shared library).
No real NATS server required.
"""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import KeyNotFoundError

import factory.adapters.nats.jetstream_audio_consumer as jac
from factory.adapters.nats.jetstream_audio_consumer import (
    MAX_DELIVER,
    JetStreamAudioConsumer,
)
from factory.adapters.nats.jetstream_audio_dedup import (
    InMemorySentSet,
    KvSentSet,
    _KvLike,
)
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    OutboundAudio,
    Platform,
)
from roxabi_contracts.blob_ref import BlobRef

# ---------------------------------------------------------------------------
# Fake KV — same shape as in test_outbound_audio_kv_dedup but local copy
# ---------------------------------------------------------------------------


class _FakeKvEntry:
    """Minimal stand-in for nats.js.kv.KeyValue.Entry."""

    def __init__(self, value: bytes) -> None:
        self.value = value


class _FakeKv:
    """Dict-backed fake satisfying _KvLike.

    Raises KeyNotFoundError for absent keys (mirrors NATS behaviour).
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> _FakeKvEntry:
        if key not in self._store:
            raise KeyNotFoundError
        return _FakeKvEntry(self._store[key])

    async def put(self, key: str, value: bytes) -> int:
        self._store[key] = value
        return len(self._store)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_DURABLE = "outbound-audio-telegram"
_FILTER = "lyra.outbound.audio.telegram.>"


def _make_consumer_kv(
    kv: _FakeKv,
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
) -> JetStreamAudioConsumer:
    """Build a JetStreamAudioConsumer backed by KvSentSet(kv)."""
    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable=_DURABLE,
        filter_subject=_FILTER,
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
        dedup=KvSentSet(cast(_KvLike, kv)),
    )


def _make_consumer_mem(
    dedup: InMemorySentSet | None = None,
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
) -> JetStreamAudioConsumer:
    """Build a JetStreamAudioConsumer with an InMemorySentSet (or fresh default)."""
    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable=_DURABLE,
        filter_subject=_FILTER,
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
        dedup=dedup,
    )


def _make_nats_msg(stream_id: str, num_delivered_val: int = 1) -> MagicMock:
    """Build a fake JetStream pull message with a valid audio envelope."""
    from roxabi_nats._serialize import serialize

    audio = OutboundAudio(
        blob_ref=BlobRef(
            store_key="key/sc6-test.ogg",
            content_hash="deadbeef",
            mime="audio/ogg",
            size=512,
            source="voicecli",
        ),
        mime_type="audio/ogg",
    )
    inbound = InboundMessage(
        id=stream_id,
        platform=Platform.TELEGRAM.value,
        bot_id="sc6-bot",
        scope_id="scope:sc6:1",
        user_id="u:sc6:1",
        user_name="sc6user",
        is_mention=False,
        text="voice",
        text_raw="voice",
        trust_level=TrustLevel.PUBLIC,
    )
    envelope = {
        "type": "audio",
        "stream_id": stream_id,
        "audio": json.loads(serialize(audio).decode("utf-8")),
        "original_msg": json.loads(serialize(inbound).decode("utf-8")),
    }
    data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    msg = MagicMock()
    msg.data = data
    msg.subject = "lyra.outbound.audio.telegram.sc6-bot"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()

    meta = MagicMock()
    meta.num_delivered = num_delivered_val
    msg.metadata = meta
    return msg


# ===========================================================================
# SC6 — Cross-restart dedup via shared KV (consumer outcome)
# ===========================================================================


@pytest.mark.anyio
async def test_sc6_cross_restart_kv_dedup_no_double_send() -> None:
    """SC6: process restart does NOT double-send when KV holds the stream_id.

    Scenario:
      1. Instance 1 sends audio successfully but ack() raises (simulates
         the ack being lost on restart/crash).
      2. JetStream redelivers the same stream_id to Instance 2 (new object,
         fresh in-memory state) that shares the SAME KV bucket.
      3. Instance 2 hits dedup (KV has the key) → does NOT call send_audio →
         acks the redelivery to unblock the consumer.

    Total send_audio calls across both instances must be exactly 1.
    """
    stream_id = "sc6-restart-001"
    shared_kv = _FakeKv()

    # --- Instance 1: original consumer ---
    send_audio_1 = AsyncMock()
    send_audio_1.return_value = None  # success
    consumer_1 = _make_consumer_kv(shared_kv, send_audio=send_audio_1)

    # First delivery succeeds but ack() raises (crash / network drop)
    msg_first = _make_nats_msg(stream_id, num_delivered_val=1)
    msg_first.ack = AsyncMock(side_effect=OSError("ack lost on restart"))

    await consumer_1._process(msg_first)

    # Verify: send_audio fired, mark_sent written to shared KV before ack()
    send_audio_1.assert_awaited_once()
    assert await KvSentSet(cast(_KvLike, shared_kv)).already_sent(stream_id), (
        "mark_sent must write to shared KV before ack() is attempted"
    )

    # --- Instance 2: simulated restart — fresh object, SAME shared KV ---
    send_audio_2 = AsyncMock()
    consumer_2 = _make_consumer_kv(shared_kv, send_audio=send_audio_2)

    # JetStream redelivers because it never saw the ack
    msg_redeliver = _make_nats_msg(stream_id, num_delivered_val=2)
    await consumer_2._process(msg_redeliver)

    # Assert: second instance must NOT call send_audio (KV dedup hit)
    send_audio_2.assert_not_awaited()
    # Redelivery must be acked to unblock the consumer
    msg_redeliver.ack.assert_awaited_once()
    # No terminal path
    msg_redeliver.term.assert_not_awaited()

    # Combined: exactly 1 send across both instances
    total_calls = send_audio_1.await_count + send_audio_2.await_count
    assert total_calls == 1, (
        f"Expected exactly 1 send_audio call across both instances, got {total_calls}"
    )


@pytest.mark.anyio
async def test_sc6_negative_in_memory_does_not_dedup_across_instances() -> None:
    """SC6 negative contrast: InMemorySentSet does NOT survive instance recreation.

    With InMemorySentSet, a second consumer instance (independent object) has
    a fresh empty dedup set — it WOULD re-send the same stream_id.
    This test makes the value of persistent KV explicit: deleting KvSentSet
    and using per-instance InMemorySentSet would restore the double-send bug.
    """
    stream_id = "sc6-mem-contrast-001"

    # Instance 1: in-memory dedup — sends successfully
    send_audio_1 = AsyncMock()
    consumer_1 = _make_consumer_mem(send_audio=send_audio_1)

    msg_first = _make_nats_msg(stream_id, num_delivered_val=1)
    await consumer_1._process(msg_first)
    send_audio_1.assert_awaited_once()

    # Instance 2: brand-new InMemorySentSet — knows nothing about Instance 1's history
    send_audio_2 = AsyncMock()
    consumer_2 = _make_consumer_mem(
        InMemorySentSet(),  # fresh, empty
        send_audio=send_audio_2,
    )

    msg_redeliver = _make_nats_msg(stream_id, num_delivered_val=2)
    await consumer_2._process(msg_redeliver)

    # With in-memory dedup: second instance re-sends (the gap that KV fixes).
    # assert_awaited_once() raises if the call did not happen exactly once.
    send_audio_2.assert_awaited_once()


# ===========================================================================
# SC9 — Terminal drop metric increments (before/after delta pattern)
# ===========================================================================


@pytest.mark.anyio
async def test_sc9_terminal_drop_metric_increments_by_one() -> None:
    """SC9: audio_terminal_drop_total increments by exactly 1 on terminal path.

    Uses before/after delta pattern — safe under pytest-xdist (-n auto)
    and across parallel test runs without monkeypatching shared state.
    Reads the counter directly from the module so the test observes the
    same global that _handle_terminal mutates.
    """
    # Arrange
    send_audio = AsyncMock(side_effect=RuntimeError("platform down"))
    send_text = AsyncMock()
    consumer = _make_consumer_mem(send_audio=send_audio, send_text=send_text)

    msg = _make_nats_msg("sc9-terminal-001", num_delivered_val=MAX_DELIVER)

    before = jac.audio_terminal_drop_total

    # Act — drives the terminal path: num_delivered >= max_deliver + failing send
    await consumer._process(msg)

    after = jac.audio_terminal_drop_total

    # Assert: exactly one increment
    assert after - before == 1, (
        f"Expected audio_terminal_drop_total to increment by 1; "
        f"before={before}, after={after}, delta={after - before}"
    )

    # Corroborate: term() called on the message (terminal path taken)
    msg.term.assert_awaited_once()
    # No ack (terminal messages are termed, not acked)
    msg.ack.assert_not_awaited()


@pytest.mark.anyio
async def test_sc9_terminal_drop_metric_not_incremented_on_success() -> None:
    """SC9 negative: successful send does NOT increment audio_terminal_drop_total.

    Deleting the counter increment from _handle_terminal would cause
    test_sc9_terminal_drop_metric_increments_by_one to fail; this test
    confirms the counter is NOT incremented on the happy path (guards
    against over-counting).
    """
    send_audio = AsyncMock()  # succeeds
    consumer = _make_consumer_mem(send_audio=send_audio)
    msg = _make_nats_msg("sc9-success-001", num_delivered_val=1)

    before = jac.audio_terminal_drop_total
    await consumer._process(msg)
    after = jac.audio_terminal_drop_total

    assert after - before == 0, (
        f"audio_terminal_drop_total must not increment on success; "
        f"before={before}, after={after}"
    )


@pytest.mark.anyio
async def test_sc9_redelivery_metric_increments_by_one() -> None:
    """SC9: audio_redelivery_total increments by exactly 1 on num_delivered > 1.

    Uses the same before/after delta pattern as the terminal-drop test.
    Drives a failing-transient message (num_delivered=2) that does not reach
    the terminal threshold so only the redelivery counter is touched.
    """
    # num_delivered=2 < MAX_DELIVER=5 → transient path (no term), redelivery observed
    send_audio = AsyncMock(side_effect=OSError("transient"))
    consumer = _make_consumer_mem(send_audio=send_audio)
    msg = _make_nats_msg("sc9-redeliver-001", num_delivered_val=2)

    before = jac.audio_redelivery_total
    await consumer._process(msg)
    after = jac.audio_redelivery_total

    assert after - before == 1, (
        f"Expected audio_redelivery_total to increment by 1; "
        f"before={before}, after={after}, delta={after - before}"
    )

    # Sanity: not acked (transient — JetStream will redeliver)
    msg.ack.assert_not_awaited()
    msg.term.assert_not_awaited()


@pytest.mark.anyio
async def test_sc9_redelivery_metric_not_incremented_on_first_delivery() -> None:
    """SC9 negative: first delivery does NOT increment redelivery counter.

    num_delivered=1 → audio_redelivery_total must stay unchanged.
    """
    send_audio = AsyncMock()
    consumer = _make_consumer_mem(send_audio=send_audio)
    msg = _make_nats_msg("sc9-first-001", num_delivered_val=1)

    before = jac.audio_redelivery_total
    await consumer._process(msg)
    after = jac.audio_redelivery_total

    assert after - before == 0, (
        f"audio_redelivery_total must not increment on first delivery; "
        f"before={before}, after={after}"
    )
