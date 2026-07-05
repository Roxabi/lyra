"""Strip hub-local fields before cross-process wire serialization."""

from __future__ import annotations

import dataclasses

from factory.core.messaging.message import InboundMessage, OutboundMessage

_HUB_LOCAL_METADATA_KEYS: frozenset[str] = frozenset({"_on_dispatched"})


def wire_inbound(msg: InboundMessage) -> InboundMessage:
    """InboundMessage safe for NATS publish (no callables / pending carriers)."""
    if (
        msg.session_update_fn is None
        and msg.pending_attachment is None
        and not msg.pending_attachments
    ):
        return msg
    return dataclasses.replace(
        msg,
        session_update_fn=None,
        pending_attachment=None,
        pending_attachments=(),
    )


def wire_outbound(outbound: OutboundMessage) -> OutboundMessage:
    """OutboundMessage safe for NATS publish (no hub-local metadata)."""
    if not outbound.metadata or not (
        _HUB_LOCAL_METADATA_KEYS & outbound.metadata.keys()
    ):
        return outbound
    cleaned = {
        k: v
        for k, v in outbound.metadata.items()
        if k not in _HUB_LOCAL_METADATA_KEYS
    }
    return dataclasses.replace(outbound, metadata=cleaned)
