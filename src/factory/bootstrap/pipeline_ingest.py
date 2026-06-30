"""Hub pipeline ingest — factory.event.* → PipelineStore."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nats.aio.msg import Msg
from pydantic import ValidationError

from factory.infrastructure.events.stream_setup import STREAM_EVENTS
from factory.nats.fleet_store import FleetStore
from factory.nats.pipeline_store import PipelineStore
from roxabi_contracts.event.models import LyraEvent
from roxabi_nats._serialize import deserialize

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.js.client import JetStreamContext

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 1_000_000
_PIPELINE_DURABLE = "factory-pipeline-projector"


async def start_pipeline_ingest(hub: Hub, nc: NATS, js: JetStreamContext) -> list[Any]:
    """Subscribe to ingress events and fleet reports; attach PipelineStore to hub."""
    store = PipelineStore()
    hub._pipeline_store = store  # noqa: SLF001

    async def _on_event(msg: Msg) -> None:
        if len(msg.data) > _MAX_PAYLOAD_BYTES:
            return
        try:
            event = deserialize(msg.data, LyraEvent)
        except (ValidationError, ValueError, UnicodeDecodeError):
            log.warning("pipeline_ingest: invalid LyraEvent", exc_info=True)
            return
        service = event.service
        kind = event.kind
        payload = event.payload if isinstance(event.payload, dict) else {}
        trace = event.trace_id
        if service == "github":
            store.apply_github_event(kind=kind, payload=payload, trace_id=trace)
        elif service == "cloudflare":
            store.apply_cloudflare_event(kind=kind, trace_id=trace)
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