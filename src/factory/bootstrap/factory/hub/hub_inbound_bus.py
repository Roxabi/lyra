"""Build inbound bus for hub."""

from __future__ import annotations

import logging

from nats.aio.client import Client as NATS

from factory.bootstrap.factory.config import InboundBusConfig, _load_inbound_bus_config
from factory.core.messaging.message import InboundMessage
from factory.nats.nats_bus import NatsBus
from factory.nats.queue_groups import HUB_INBOUND

log = logging.getLogger(__name__)


def build_inbound_bus(
    nc: NATS, raw_config: dict
) -> tuple[NatsBus[InboundMessage], InboundBusConfig]:
    """Create NatsBus for inbound messages and return (bus, inbound_bus_cfg)."""
    inbound_bus_cfg = _load_inbound_bus_config(raw_config)
    inbound_bus: NatsBus[InboundMessage] = NatsBus(
        nc=nc,
        bot_id="hub",
        item_type=InboundMessage,
        staging_maxsize=inbound_bus_cfg.staging_maxsize,
        queue_group=HUB_INBOUND,
    )
    return inbound_bus, inbound_bus_cfg
