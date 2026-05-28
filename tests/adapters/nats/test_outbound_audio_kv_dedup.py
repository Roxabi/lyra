"""Tests for KvSentSet and cross-restart dedup guarantee (#1482, T10).

Covers:
  - KvSentSet.already_sent returns False on KeyNotFoundError (absent key).
  - KvSentSet.already_sent returns True after mark_sent writes a key.
  - Cross-restart scenario: stream_id marked via KV, new consumer instance
    (fresh in-memory state) sharing the SAME KV → already_sent returns True
    → redelivered message is acked WITHOUT a second send.

Fakes: dict-backed KeyValue stub — no real NATS server.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from nats.js.errors import KeyNotFoundError

from lyra.adapters.nats.jetstream_audio_consumer import JetStreamAudioConsumer
from lyra.adapters.nats.jetstream_audio_dedup import KvSentSet, _encode_key
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAudio,
    Platform,
)
from roxabi_contracts.blob_ref import BlobRef

# ---------------------------------------------------------------------------
# Fake KV backed by a plain dict — shared across consumer instances
# ---------------------------------------------------------------------------


class FakeKvEntry:
    """Minimal stand-in for nats.js.kv.KeyValue.Entry."""

    def __init__(self, value: bytes) -> None:
        self.value = value


class FakeKv:
    """Dict-backed fake for nats.js.kv.KeyValue.

    Supports get / put with the same async interface.
    Raises KeyNotFoundError when a key is absent (mirrors NATS behaviour).
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> FakeKvEntry:
        if key not in self._store:
            raise KeyNotFoundError
        return FakeKvEntry(self._store[key])

    async def put(self, key: str, value: bytes) -> int:
        self._store[key] = value
        return len(self._store)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DURABLE = "outbound-audio-telegram"
_FILTER = "lyra.outbound.audio.telegram.>"


def _make_consumer_with_kv(
    kv: FakeKv,
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
) -> JetStreamAudioConsumer:
    """Build a JetStreamAudioConsumer injecting KvSentSet(kv)."""
    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable=_DURABLE,
        filter_subject=_FILTER,
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
        dedup=KvSentSet(kv),
    )


def _make_nats_msg(stream_id: str, num_delivered_val: int = 1) -> MagicMock:
    from roxabi_nats._serialize import serialize

    audio = OutboundAudio(
        blob_ref=BlobRef(
            store_key="key/audio-kv-test.ogg",
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
        bot_id="kv-bot",
        scope_id="scope:kv:1",
        user_id="u:kv:1",
        user_name="kvuser",
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
    msg.subject = "lyra.outbound.audio.telegram.kv-bot"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()

    meta = MagicMock()
    meta.num_delivered = num_delivered_val
    msg.metadata = meta
    return msg


# ===========================================================================
# KvSentSet unit tests
# ===========================================================================


@pytest.mark.anyio
async def test_kv_already_sent_false_when_key_absent() -> None:
    """KeyNotFoundError from kv.get → already_sent returns False."""
    kv = FakeKv()
    s = KvSentSet(kv)
    assert not await s.already_sent("stream-absent")


@pytest.mark.anyio
async def test_kv_already_sent_true_after_mark_sent() -> None:
    """After mark_sent writes the key, already_sent returns True."""
    kv = FakeKv()
    s = KvSentSet(kv)

    await s.mark_sent("stream-present")
    assert await s.already_sent("stream-present")


@pytest.mark.anyio
async def test_kv_mark_sent_writes_encoded_key() -> None:
    """mark_sent stores the hex-encoded stream_id in the KV bucket."""
    kv = FakeKv()
    s = KvSentSet(kv)

    stream_id = "msg:with:colons"
    await s.mark_sent(stream_id)

    expected_key = _encode_key(stream_id)
    assert expected_key in kv._store
    assert kv._store[expected_key] == b"1"


@pytest.mark.anyio
async def test_kv_encode_handles_special_chars() -> None:
    """stream_ids with NATS-illegal chars are safely encoded."""
    kv = FakeKv()
    s = KvSentSet(kv)

    # These chars are illegal in NATS KV keys but must not raise
    for sid in ["a.b.c", "a*b", "a>b", "a b", "a:b:c"]:
        await s.mark_sent(sid)
        assert await s.already_sent(sid), f"should be found after mark: {sid!r}"


@pytest.mark.anyio
async def test_kv_different_stream_ids_independent() -> None:
    """Two stream_ids are stored and retrieved independently."""
    kv = FakeKv()
    s = KvSentSet(kv)

    await s.mark_sent("sid-x")
    assert await s.already_sent("sid-x")
    assert not await s.already_sent("sid-y")


# ===========================================================================
# Cross-restart scenario: shared KV survives consumer re-instantiation
# ===========================================================================


@pytest.mark.anyio
async def test_cross_restart_dedup_via_kv() -> None:
    """Cross-restart: stream_id marked sent via KV → NEW consumer instance
    (simulating restart, fresh in-memory state) sharing the SAME KV →
    already_sent returns True → redelivered message is acked WITHOUT a second
    send.

    This is the core guarantee of V2 (KV-backed dedup): a process restart
    does NOT cause a double-send for messages already delivered.
    """
    stream_id = "restart-test-001"
    shared_kv = FakeKv()

    # --- Instance 1: original consumer process ---
    send_audio_1 = AsyncMock()
    consumer_1 = _make_consumer_with_kv(shared_kv, send_audio=send_audio_1)

    msg_first = _make_nats_msg(stream_id, num_delivered_val=1)
    await consumer_1._process(msg_first)

    # Verify: first consumer sent and acked
    send_audio_1.assert_awaited_once()
    msg_first.ack.assert_awaited_once()

    # Verify: stream_id now in shared KV
    assert await KvSentSet(shared_kv).already_sent(stream_id)

    # --- Instance 2: new consumer after simulated process restart ---
    # Fresh in-memory state (new object) but SAME shared_kv
    send_audio_2 = AsyncMock()
    consumer_2 = _make_consumer_with_kv(shared_kv, send_audio=send_audio_2)

    # JetStream redelivers because it did not observe the ack (restart race)
    msg_redeliver = _make_nats_msg(stream_id, num_delivered_val=2)
    await consumer_2._process(msg_redeliver)

    # Assert: second consumer DID NOT call send_audio (cross-restart dedup hit)
    send_audio_2.assert_not_awaited()
    # Redelivered message was acked (dedup-hit path)
    msg_redeliver.ack.assert_awaited_once()
    msg_redeliver.term.assert_not_awaited()


@pytest.mark.anyio
async def test_cross_restart_new_stream_id_is_delivered() -> None:
    """After restart, a DIFFERENT stream_id (new message) is still delivered.

    Ensures the KV dedup does not suppress genuinely new messages.
    """
    shared_kv = FakeKv()

    # Instance 1 delivers stream_id A
    send_audio_1 = AsyncMock()
    consumer_1 = _make_consumer_with_kv(shared_kv, send_audio=send_audio_1)
    await consumer_1._process(_make_nats_msg("sid-A"))
    send_audio_1.assert_awaited_once()

    # Instance 2 (post-restart) receives a new stream_id B
    send_audio_2 = AsyncMock()
    consumer_2 = _make_consumer_with_kv(shared_kv, send_audio=send_audio_2)
    msg_b = _make_nats_msg("sid-B")
    await consumer_2._process(msg_b)

    # B must be delivered (not in KV)
    send_audio_2.assert_awaited_once()
    msg_b.ack.assert_awaited_once()
