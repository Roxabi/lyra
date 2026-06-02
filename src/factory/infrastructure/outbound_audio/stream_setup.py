"""JetStream stream + durable consumer + KV bootstrap for outbound-audio.

Stream LYRA_OUTBOUND_AUDIO: subject lyra.outbound.audio.>, retention Limits
(NOT WorkQueue — multiple per-platform consumers attach, N×M fan-out),
MaxAge=24h, MaxBytes=32MiB, duplicate_window=60s.

Consumer (durable, pull): AckExplicit, AckWait=90s, MaxDeliver=5,
filter_subject parameterised by bootstrap caller (e.g.
lyra.outbound.audio.telegram.{bot_id} — exact 5-token subject, no ".>").

KV bucket lyra_outbound_audio_sent: TTL=900s.
  Arithmetic: ack_wait × max_deliver = 90 × 5 = 450s floor;
  900s (15 min) gives ≥2× headroom for retry jitter and slow consumers
  while bounding the dedup-key storage window.

All three functions are idempotent — safe to call on every process boot.
They mirror the add→BadRequestError→update / consumer_info→NotFoundError→add_consumer
pattern used in factory.infrastructure.turn_writer.stream_setup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    KeyValueConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import BadRequestError, NotFoundError
from nats.js.kv import KeyValue

from roxabi_contracts.outbound import STREAM_AUDIO

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream constants
# ---------------------------------------------------------------------------

STREAM_SUBJECTS = ["lyra.outbound.audio.>"]
ACK_WAIT_SECONDS = 90.0
MAX_DELIVER = 5
MAX_AGE_SECONDS = 24 * 60 * 60  # 24 h — silent-loss bound (D4)
MAX_BYTES = 32 * 1024 * 1024  # 32 MiB
DUPLICATE_WINDOW_SECONDS = 60  # 60 s dedup window

# KV bucket: TTL = ack_wait × max_deliver × 2 (headroom)
# Floor: 90 × 5 = 450 s; we use 900 s (15 min) for ≥2× retry-jitter margin.
KV_BUCKET = "lyra_outbound_audio_sent"
KV_TTL_SECONDS = 900.0  # 90 × 5 = 450 s floor → 900 s (15 min) with headroom


# ---------------------------------------------------------------------------
# Config builders (internal)
# ---------------------------------------------------------------------------


def _stream_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM_AUDIO,
        subjects=STREAM_SUBJECTS,
        retention=RetentionPolicy.LIMITS,  # N×M consumers; NOT WorkQueue
        storage=StorageType.FILE,
        max_age=float(MAX_AGE_SECONDS),
        max_bytes=MAX_BYTES,
        duplicate_window=DUPLICATE_WINDOW_SECONDS,  # nats-py converts to ns internally
    )


def _consumer_config(*, durable: str, filter_subject: str) -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=durable,
        name=durable,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ACK_WAIT_SECONDS,
        max_deliver=MAX_DELIVER,
        filter_subject=filter_subject,
    )


def _kv_config() -> KeyValueConfig:
    return KeyValueConfig(
        bucket=KV_BUCKET,
        ttl=KV_TTL_SECONDS,
        storage=StorageType.FILE,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def ensure_stream(js: "JetStreamContext") -> None:
    """Create or update LYRA_OUTBOUND_AUDIO stream idempotently.

    Pattern: try add_stream first; on BadRequestError (already exists) try
    update_stream to converge config. Any other nats.errors.Error is re-raised.
    """
    cfg = _stream_config()
    try:
        await js.add_stream(cfg)
        log.info("outbound-audio: stream %s created", STREAM_AUDIO)
    except BadRequestError:
        try:
            await js.update_stream(cfg)
            log.info("outbound-audio: stream %s config updated", STREAM_AUDIO)
        except nats.errors.Error:
            log.exception("outbound-audio: stream %s update failed", STREAM_AUDIO)
            raise
    except nats.errors.Error:
        log.exception("outbound-audio: stream %s add failed", STREAM_AUDIO)
        raise


async def ensure_consumer(
    js: "JetStreamContext",
    *,
    durable: str,
    filter_subject: str,
) -> None:
    """Create durable pull consumer idempotently.

    Checks consumer_info first; creates if NotFoundError. If the consumer
    already exists the config is left as-is (matching durable name → no-op).

    Args:
        js: JetStreamContext bound to the NATS connection.
        durable: Durable consumer name (e.g. "outbound-audio-telegram").
        filter_subject: Exact per-bot subject (e.g.
            "lyra.outbound.audio.telegram.123456") — no trailing ".>".
    """
    cfg = _consumer_config(durable=durable, filter_subject=filter_subject)
    try:
        await js.consumer_info(STREAM_AUDIO, durable)
        log.debug("outbound-audio: consumer %s already exists", durable)
    except NotFoundError:
        try:
            await js.add_consumer(STREAM_AUDIO, config=cfg)
            log.info(
                "outbound-audio: consumer %s created on stream %s",
                durable,
                STREAM_AUDIO,
            )
        except nats.errors.Error:
            log.exception(
                "outbound-audio: consumer %s add failed on stream %s",
                durable,
                STREAM_AUDIO,
            )
            raise


async def ensure_kv(js: "JetStreamContext") -> KeyValue:
    """Create or bind KV bucket lyra_outbound_audio_sent idempotently.

    TTL=900s (15 min).
    Arithmetic: ack_wait=90s × max_deliver=5 = 450s floor; 900s ≥2× headroom
    for retry jitter and slow consumers.

    Returns the bound KeyValue handle.
    """
    cfg = _kv_config()
    try:
        kv = await js.create_key_value(cfg)
        log.info("outbound-audio: KV bucket %s created", KV_BUCKET)
        return kv
    except BadRequestError:
        # BadRequestError = bucket already exists (create_key_value on existing bucket).
        # BucketNotFoundError is a read-time error from key_value(), not from create.
        log.debug("outbound-audio: KV bucket %s already exists, binding", KV_BUCKET)
        return await js.key_value(KV_BUCKET)
    except nats.errors.Error:
        log.exception("outbound-audio: KV bucket %s provision failed", KV_BUCKET)
        raise
