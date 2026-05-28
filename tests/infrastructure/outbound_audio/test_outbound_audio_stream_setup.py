"""Tests for outbound_audio/stream_setup.py public API.

Verifies idempotency: calling ensure_stream / ensure_consumer / ensure_kv
twice does not raise. All tests use fake/mock JetStreamContext — no live
NATS server required.

Pattern mirrors tests/infrastructure/turn_writer/test_stream_setup.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from nats.js.errors import BadRequestError, NotFoundError

from lyra.infrastructure.outbound_audio.stream_setup import (
    KV_BUCKET,
    KV_TTL_SECONDS,
    _consumer_config,
    _kv_config,
    _stream_config,
    ensure_consumer,
    ensure_kv,
    ensure_stream,
)
from roxabi_contracts.outbound import STREAM_AUDIO

# ---------------------------------------------------------------------------
# ensure_stream
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_stream_skips_update_when_add_succeeds() -> None:
    """add_stream succeeds → update_stream must NOT be called (idempotent path 1)."""
    js = MagicMock()
    js.add_stream = AsyncMock(return_value=MagicMock())
    js.update_stream = AsyncMock()

    await ensure_stream(js)

    js.add_stream.assert_awaited_once()
    js.update_stream.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_stream_updates_when_already_exists() -> None:
    """add_stream raises BadRequestError → update_stream called (idempotent path 2)."""
    js = MagicMock()
    js.add_stream = AsyncMock(side_effect=BadRequestError())
    js.update_stream = AsyncMock(return_value=MagicMock())
    expected_cfg = _stream_config()

    await ensure_stream(js)

    js.add_stream.assert_awaited_once()
    js.update_stream.assert_awaited_once()
    assert js.update_stream.await_args == call(expected_cfg)


@pytest.mark.anyio
async def test_ensure_stream_idempotent_double_call() -> None:
    """Calling ensure_stream twice on a fresh stream does not raise."""
    js = MagicMock()
    # First call: add succeeds; second call: add raises BadRequestError (exists)
    js.add_stream = AsyncMock(side_effect=[MagicMock(), BadRequestError()])
    js.update_stream = AsyncMock(return_value=MagicMock())

    await ensure_stream(js)
    await ensure_stream(js)  # must not raise

    assert js.add_stream.await_count == 2
    js.update_stream.assert_awaited_once()


# ---------------------------------------------------------------------------
# ensure_consumer
# ---------------------------------------------------------------------------

DURABLE = "outbound-audio-telegram"
FILTER = "lyra.outbound.audio.telegram.>"


@pytest.mark.anyio
async def test_ensure_consumer_skips_create_when_exists() -> None:
    """consumer_info returns a value → add_consumer must NOT be called."""
    js = MagicMock()
    js.consumer_info = AsyncMock(return_value=MagicMock())
    js.add_consumer = AsyncMock()

    await ensure_consumer(js, durable=DURABLE, filter_subject=FILTER)

    js.consumer_info.assert_awaited_once_with(STREAM_AUDIO, DURABLE)
    js.add_consumer.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_consumer_creates_when_not_found() -> None:
    """consumer_info raises NotFoundError → add_consumer called with correct args."""
    js = MagicMock()
    js.consumer_info = AsyncMock(side_effect=NotFoundError())
    js.add_consumer = AsyncMock(return_value=MagicMock())
    expected_cfg = _consumer_config(durable=DURABLE, filter_subject=FILTER)

    await ensure_consumer(js, durable=DURABLE, filter_subject=FILTER)

    js.consumer_info.assert_awaited_once_with(STREAM_AUDIO, DURABLE)
    js.add_consumer.assert_awaited_once()
    assert js.add_consumer.await_args == call(STREAM_AUDIO, config=expected_cfg)


@pytest.mark.anyio
async def test_ensure_consumer_idempotent_double_call() -> None:
    """Calling ensure_consumer twice on an existing consumer does not raise."""
    js = MagicMock()
    js.consumer_info = AsyncMock(return_value=MagicMock())
    js.add_consumer = AsyncMock()

    await ensure_consumer(js, durable=DURABLE, filter_subject=FILTER)
    await ensure_consumer(js, durable=DURABLE, filter_subject=FILTER)

    assert js.consumer_info.await_count == 2
    js.add_consumer.assert_not_awaited()


# ---------------------------------------------------------------------------
# ensure_kv
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_kv_creates_bucket_when_absent() -> None:
    """create_key_value succeeds → returns KeyValue handle, key_value not called."""
    fake_kv = MagicMock()
    js = MagicMock()
    js.create_key_value = AsyncMock(return_value=fake_kv)
    js.key_value = AsyncMock()

    result = await ensure_kv(js)

    js.create_key_value.assert_awaited_once()
    js.key_value.assert_not_awaited()
    assert result is fake_kv


@pytest.mark.anyio
async def test_ensure_kv_binds_when_already_exists() -> None:
    """create_key_value raises BadRequestError → key_value called to bind existing."""
    fake_kv = MagicMock()
    js = MagicMock()
    js.create_key_value = AsyncMock(side_effect=BadRequestError())
    js.key_value = AsyncMock(return_value=fake_kv)

    result = await ensure_kv(js)

    js.key_value.assert_awaited_once_with(KV_BUCKET)
    assert result is fake_kv


@pytest.mark.anyio
async def test_ensure_kv_idempotent_double_call() -> None:
    """Calling ensure_kv twice (second sees existing bucket) does not raise."""
    fake_kv = MagicMock()
    js = MagicMock()
    # First call: create succeeds; second: BadRequestError (exists)
    js.create_key_value = AsyncMock(
        side_effect=[fake_kv, BadRequestError()]
    )
    js.key_value = AsyncMock(return_value=fake_kv)

    kv1 = await ensure_kv(js)
    kv2 = await ensure_kv(js)  # must not raise

    assert kv1 is fake_kv
    assert kv2 is fake_kv
    assert js.create_key_value.await_count == 2


# ---------------------------------------------------------------------------
# Config sanity checks
# ---------------------------------------------------------------------------


def test_kv_ttl_exceeds_ack_floor() -> None:
    """KV TTL must exceed ack_wait × max_deliver floor (450 s)."""
    from lyra.infrastructure.outbound_audio.stream_setup import (
        ACK_WAIT_SECONDS,
        MAX_DELIVER,
    )

    floor = ACK_WAIT_SECONDS * MAX_DELIVER  # 90 × 5 = 450
    assert KV_TTL_SECONDS > floor, (
        f"KV_TTL_SECONDS ({KV_TTL_SECONDS}) must exceed "
        f"ack_wait×max_deliver floor ({floor})"
    )


def test_stream_uses_limits_retention() -> None:
    """Stream must use Limits retention (not WorkQueue) for N×M consumer fan-out."""
    from nats.js.api import RetentionPolicy

    cfg = _stream_config()
    assert cfg.retention == RetentionPolicy.LIMITS


def test_kv_bucket_name() -> None:
    """KV bucket name must match contract constant."""
    cfg = _kv_config()
    assert cfg.bucket == KV_BUCKET == "lyra_outbound_audio_sent"


def test_stream_name_matches_contract() -> None:
    """Stream name must match STREAM_AUDIO from roxabi_contracts.outbound."""
    cfg = _stream_config()
    assert cfg.name == STREAM_AUDIO == "LYRA_OUTBOUND_AUDIO"
