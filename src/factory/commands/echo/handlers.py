"""Echo plugin handlers (issue #106)."""

from __future__ import annotations

from factory.core.messaging.message import InboundMessage, Response
from factory.core.pool import Pool


async def cmd_echo(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Echo back the provided arguments."""
    text = " ".join(args) if args else ""
    return Response(content=text)
