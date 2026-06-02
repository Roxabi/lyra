from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..agent import AgentBase
    from ..messaging.message import InboundMessage, OutboundMessage, Response
    from ..messaging.render_events import RenderEvent


@runtime_checkable
class PoolContext(Protocol):
    """Narrow interface that Pool needs from its owner (typically Hub).

    Decouples Pool from the full Hub, enabling isolated testing and
    reducing coupling (ADR-017, issue #204).
    """

    def get_agent(self, name: str) -> AgentBase | None: ...

    def get_message(self, key: str) -> str | None: ...

    async def dispatch_response(
        self, msg: InboundMessage, response: Response | OutboundMessage
    ) -> None: ...

    async def dispatch_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None: ...

    def record_circuit_success(self) -> None: ...

    def record_circuit_failure(self, exc: BaseException) -> None: ...
