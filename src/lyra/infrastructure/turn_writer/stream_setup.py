"""JetStream stream + durable consumer bootstrap for TurnWriter.

Stream LYRA_TURNS: subject lyra.turns.>, retention WorkQueue,
MaxAge=24h, MaxBytes=256MiB. Idempotent — safe to call on each writer boot.

Consumer turn-writer-v1: durable, AckExplicit, AckWait=60s, MaxDeliver=5,
filter_subject=lyra.turns.write.

Note: nats-py does not expose a queue_group field on ConsumerConfig directly —
  queue group semantics are achieved via durable name sharing across replicas.
  If horizontal scaling is needed, all replicas bind to the same durable consumer
  name (turn-writer-v1) via pull_subscribe_bind; nats-py + NATS server handle
  load-balanced delivery internally.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import BadRequestError, NotFoundError

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

STREAM_NAME = "LYRA_TURNS"
CONSUMER_NAME = "turn-writer-v1"
QUEUE_GROUP = "turn-writer"
SUBJECT_FILTER = "lyra.turns.write"
ACK_WAIT_SECONDS = 60.0
MAX_DELIVER = 5
MAX_AGE_SECONDS = 24 * 60 * 60  # 24 h
MAX_BYTES = 256 * 1024 * 1024   # 256 MiB


def _stream_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM_NAME,
        subjects=["lyra.turns.>"],
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        max_age=float(MAX_AGE_SECONDS),
        max_bytes=MAX_BYTES,
        duplicate_window=60,  # seconds — nats-py converts to ns internally
    )


def _consumer_config() -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=CONSUMER_NAME,
        name=CONSUMER_NAME,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ACK_WAIT_SECONDS,
        max_deliver=MAX_DELIVER,
        filter_subject=SUBJECT_FILTER,
    )


async def ensure_stream(js: "JetStreamContext") -> None:
    """Create or update LYRA_TURNS stream idempotently.

    Pattern mirrors JetStreamAuditSink.provision: try add_stream first;
    on BadRequestError (already exists) try update_stream to converge config.
    Any other nats.errors.Error is re-raised for the caller to handle.
    """
    cfg = _stream_config()
    try:
        await js.add_stream(cfg)
        log.info("turn-writer: stream %s created", STREAM_NAME)
    except BadRequestError:
        try:
            await js.update_stream(cfg)
            log.info("turn-writer: stream %s config updated", STREAM_NAME)
        except nats.errors.Error:
            log.exception("turn-writer: stream %s update failed", STREAM_NAME)
            raise
    except nats.errors.Error:
        log.exception("turn-writer: stream %s add failed", STREAM_NAME)
        raise


async def ensure_consumer(js: "JetStreamContext") -> None:
    """Create or update durable consumer turn-writer-v1 idempotently.

    Checks consumer_info first; creates if NotFoundError. If the consumer
    already exists the config is left as-is (nats-py add_consumer on an
    existing durable returns the existing info, effectively a no-op for
    matching configs).
    """
    cfg = _consumer_config()
    try:
        await js._jsm.consumer_info(STREAM_NAME, CONSUMER_NAME)  # noqa: SLF001
        log.debug("turn-writer: consumer %s already exists", CONSUMER_NAME)
    except NotFoundError:
        try:
            await js._jsm.add_consumer(STREAM_NAME, config=cfg)  # noqa: SLF001
            log.info(
                "turn-writer: consumer %s created on stream %s",
                CONSUMER_NAME,
                STREAM_NAME,
            )
        except nats.errors.Error:
            log.exception(
                "turn-writer: consumer %s add failed on stream %s",
                CONSUMER_NAME,
                STREAM_NAME,
            )
            raise
