"""Echo plugin handlers (issue #106)."""

from __future__ import annotations

from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool


async def cmd_echo(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Echo back the provided arguments."""
    text = " ".join(args) if args else ""
    return Response(content=text)
