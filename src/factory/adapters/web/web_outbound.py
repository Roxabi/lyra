"""Outbound delivery for the web smoke adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory.adapters.web.web_formatter import WebFormatter, web_session_id
from factory.core.messaging.message import InboundMessage, OutboundMessage
from factory.outbound.emitter import OutboundEmitter
from factory.outbound.error_handler import OutboundErrorHandler

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter


async def send(
    adapter: "WebAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage,
) -> None:
    """Push a complete reply to the browser session."""
    session_id = web_session_id(original_msg)
    text = outbound.to_text()
    await adapter.sessions.publish(session_id, {"type": "delta", "text": text})
    await adapter.sessions.publish(session_id, {"type": "done"})


def _make_emitter(
    adapter: "WebAdapter",
    original_msg: InboundMessage,
    outbound: OutboundMessage | None,
) -> OutboundEmitter:
    session_id = web_session_id(original_msg)
    formatter = WebFormatter(adapter.sessions, session_id)
    handler = OutboundErrorHandler(get_msg=formatter.get_msg)
    return OutboundEmitter(formatter, outbound, error_handler=handler)
