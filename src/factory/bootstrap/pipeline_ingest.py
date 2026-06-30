"""Hub pipeline ingest — factory.event.* → PipelineStore."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import nats.errors
from nats.aio.msg import Msg
from pydantic import ValidationError

from factory.infrastructure.events.stream_setup import (
    STREAM_EVENTS,
    _EVENTS_MAX_AGE_SECONDS,
)
from factory.nats.fleet_store import FleetStore
from factory.nats.pipeline import PipelineStore
from roxabi_contracts.event.models import LyraEvent
from roxabi_nats._serialize import deserialize

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.js.client import JetStreamContext

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 1_000_000
_PIPELINE_DURABLE = "factory-pipeline-projector"
_REPLAY_BATCH = 100
_REPLAY_FETCH_TIMEOUT_S = 2.0


def apply_lyra_event(store: PipelineStore, event: LyraEvent) -> None:
    """Apply one ingress LyraEvent to the pipeline read model."""
    service = event.service
    kind = event.kind
    payload = event.payload if isinstance(event.payload, dict) else {}
    trace = event.trace_id
    if service == "github":
        store.apply_github_event(kind=kind, payload=payload, trace_id=trace)
    elif service == "cloudflare":
        store.apply_cloudflare_event(kind=kind, payload=payload, trace_id=trace)
    elif service == "host":
        store.apply_host_event(kind=kind, payload=payload, trace_id=trace)


async def replay_jetstream_if_empty(
    store: PipelineStore, js: JetStreamContext
) -> int:
    """One-shot replay of factory-events (24 h) when the read model is empty."""
    if store.has_runs():
        log.info("pipeline_ingest: store populated — skip JetStream replay")
        return 0

    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

    start = datetime.now(tz=UTC) - timedelta(seconds=_EVENTS_MAX_AGE_SECONDS)
    config = ConsumerConfig(
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=start,
        ack_policy=AckPolicy.NONE,
    )
    sub = await js.pull_subscribe(
        "factory.event.>",
        stream=STREAM_EVENTS,
        config=config,
    )
    applied = 0
    try:
        while True:
            try:
                msgs = await sub.fetch(_REPLAY_BATCH, timeout=_REPLAY_FETCH_TIMEOUT_S)
            except nats.errors.TimeoutError:
                break
            for msg in msgs:
                if len(msg.data) > _MAX_PAYLOAD_BYTES:
                    continue
                try:
                    event = deserialize(msg.data, LyraEvent)
                except (ValidationError, ValueError, UnicodeDecodeError):
                    log.warning(
                        "pipeline_ingest: replay skipped invalid LyraEvent",
                        exc_info=True,
                    )
                    continue
                apply_lyra_event(store, event)
                applied += 1
    finally:
        await sub.unsubscribe()

    log.info(
        "pipeline_ingest: JetStream replay applied %d events (window=%dh)",
        applied,
        _EVENTS_MAX_AGE_SECONDS // 3600,
    )
    return applied


async def start_pipeline_ingest(hub: Hub, nc: NATS, js: JetStreamContext) -> list[Any]:
    """Subscribe to ingress events and fleet reports; attach PipelineStore to hub."""
    store = PipelineStore()
    hub._pipeline_store = store  # noqa: SLF001

    try:
        replayed = await replay_jetstream_if_empty(store, js)
        if replayed:
            sync_m1_deploy_from_fleet(hub, store)
    except Exception:  # noqa: BLE001 — replay is best-effort
        log.warning("pipeline_ingest: JetStream replay failed", exc_info=True)

    async def _on_event(msg: Msg) -> None:
        if len(msg.data) > _MAX_PAYLOAD_BYTES:
            return
        try:
            event = deserialize(msg.data, LyraEvent)
        except (ValidationError, ValueError, UnicodeDecodeError):
            log.warning("pipeline_ingest: invalid LyraEvent", exc_info=True)
            return
        apply_lyra_event(store, event)
        sync_m1_deploy_from_fleet(hub, store)

    subs: list[Any] = []
    try:
        sub = await js.subscribe(
            "factory.event.>",
            cb=_on_event,
            stream=STREAM_EVENTS,
            durable=_PIPELINE_DURABLE,
        )
        subs.append(sub)
        log.info("pipeline_ingest: subscribed factory.event.> stream=%s", STREAM_EVENTS)
    except Exception:  # noqa: BLE001 — degrade gracefully if JS consumer fails
        log.warning("pipeline_ingest: JetStream subscribe failed", exc_info=True)

    return subs


def sync_m1_deploy_from_fleet(hub: Hub, store: PipelineStore) -> None:
    fleet: FleetStore | None = getattr(hub, "_fleet_store", None)
    if fleet is None:
        return
    rows = []
    for snap in fleet.list_snapshot():
        rows.append(
            (
                snap.container_name,
                snap.image_revision,
                snap.status,
                snap.age_s,
            )
        )
    store.recompute_m1_from_fleet(rows)