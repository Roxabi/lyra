"""Publish plane ① host events to factory-events (JetStream)."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

import nats.errors
from nats.js.api import PubAck

from factory.ingress.normalize import build_lyra_event
from roxabi_contracts.event import per_service_event

log = logging.getLogger(__name__)


def resolve_host_machine() -> str:
    return (
        os.environ.get("FACTORY_MACHINE", "").strip()
        or os.environ.get("HOSTNAME", "").strip()
        or socket.gethostname()
    )


def host_event_kind(machine: str, kind: str) -> str:
    prefix = f"{machine}."
    if kind.startswith(prefix):
        return kind
    return f"{machine}.{kind}"


async def publish_host_event(
    nc: Any,
    *,
    machine: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    level: str = "info",
    message: str | None = None,
) -> bool:
    """Publish ``factory.event.host.<machine>.<kind>`` to JetStream."""
    full_kind = host_event_kind(machine, kind)
    event = build_lyra_event(
        service="host",
        kind=full_kind,
        level=level,
        message=message or f"host {full_kind}",
        payload=payload or {},
    )
    subject = per_service_event("host", full_kind)
    data = event.model_dump_json().encode()
    try:
        js = nc.jetstream()
        ack: PubAck = await js.publish(subject, data)
        log.info(
            "host_event published %s seq=%s trace_id=%s",
            subject,
            ack.seq,
            event.trace_id,
        )
        return True
    except (nats.errors.Error, OSError, RuntimeError) as exc:
        log.warning("host_event publish failed subject=%s: %s", subject, exc)
        return False


def parse_payload_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data