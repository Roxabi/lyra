"""Hub fleet ingest — FleetStore subscriber + catalog load."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nats.aio.msg import Msg
from pydantic import ValidationError

from factory.nats.fleet_store import FleetStore
from roxabi_contracts.fleet import CONTAINER_REPORT
from roxabi_contracts.fleet.models import ContainerReport
from roxabi_nats._serialize import deserialize

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 1_000_000


async def start_fleet_ingest(hub: Hub, nc: NATS) -> list[Any]:
    """Subscribe to container reports and attach FleetStore to hub."""
    store = FleetStore()
    hub._fleet_store = store  # noqa: SLF001

    async def _on_report(msg: Msg) -> None:
        if len(msg.data) > _MAX_PAYLOAD_BYTES:
            log.warning("fleet_ingest: payload too large (%d bytes)", len(msg.data))
            return
        try:
            report = deserialize(msg.data, ContainerReport)
        except (ValidationError, ValueError, UnicodeDecodeError):
            log.warning("fleet_ingest: invalid ContainerReport", exc_info=True)
            return
        store.upsert(report)

    sub = await nc.subscribe(CONTAINER_REPORT, cb=_on_report)
    log.info("fleet_ingest: subscribed %s", CONTAINER_REPORT)
    return [sub]