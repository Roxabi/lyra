"""factory-events / factory-metrics JetStream provisioning (ADR-079, ADR-091).

Sentinelle plane ① ingress publishes to ``factory.event.{connector}.{tenant}.{kind}``.
The hub is sole-provisioner for these monitoring streams so factory-ingress can
publish on first webhook without a manual ``bootstrap_streams.py`` step.

Retention policy (ops decision #1183):
  factory-events  — 24 h hot  (MaxAge=86400 s)
  factory-metrics — 7 d warm (MaxAge=604800 s)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

STREAM_EVENTS = "factory-events"
STREAM_METRICS = "factory-metrics"

_EVENTS_SUBJECTS = ["factory.event.>"]
_METRICS_SUBJECTS = ["factory.metric.>"]

_EVENTS_MAX_AGE_SECONDS = 24 * 60 * 60
_EVENTS_MAX_BYTES = 512 * 1024 * 1024
_METRICS_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_METRICS_MAX_BYTES = 256 * 1024 * 1024
_EVENTS_DUPLICATE_WINDOW_SECONDS = 120
_METRICS_DUPLICATE_WINDOW_SECONDS = 60


def _events_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM_EVENTS,
        subjects=_EVENTS_SUBJECTS,
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        max_age=float(_EVENTS_MAX_AGE_SECONDS),
        max_bytes=_EVENTS_MAX_BYTES,
        duplicate_window=_EVENTS_DUPLICATE_WINDOW_SECONDS,
    )


def _metrics_config() -> StreamConfig:
    return StreamConfig(
        name=STREAM_METRICS,
        subjects=_METRICS_SUBJECTS,
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        max_age=float(_METRICS_MAX_AGE_SECONDS),
        max_bytes=_METRICS_MAX_BYTES,
        duplicate_window=_METRICS_DUPLICATE_WINDOW_SECONDS,
    )


async def _ensure_stream(js: "JetStreamContext", cfg: StreamConfig) -> None:
    try:
        await js.add_stream(cfg)
        log.info("%s stream created", cfg.name)
    except BadRequestError:
        try:
            await js.update_stream(cfg)
            log.info("%s stream config updated", cfg.name)
        except nats.errors.Error:
            log.exception("%s stream update failed", cfg.name)
            raise
    except nats.errors.Error:
        log.exception("%s stream add failed", cfg.name)
        raise


async def ensure_events_stream(js: "JetStreamContext") -> None:
    """Create or update factory-events idempotently."""
    await _ensure_stream(js, _events_config())


async def ensure_metrics_stream(js: "JetStreamContext") -> None:
    """Create or update factory-metrics idempotently."""
    await _ensure_stream(js, _metrics_config())


async def ensure_observability_streams(js: "JetStreamContext") -> None:
    """Provision factory-events and factory-metrics (hub boot, ADR-079)."""
    await ensure_events_stream(js)
    await ensure_metrics_stream(js)