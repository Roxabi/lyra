"""Minimal fake NATS client for testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeNatsClient:
    """Fake NATS client for testing.

    Records published messages and subscription calls.
    Supports error injection via ``raise_on_close``.
    """

    connected: bool = True
    raise_on_close: Exception | None = None
    _published: list[tuple[str, Any]] = field(default_factory=list)
    _subscriptions: list[tuple[str, Any]] = field(default_factory=list)

    async def close(self) -> None:
        """Record close and optionally raise."""
        self.connected = False
        if self.raise_on_close:
            raise self.raise_on_close

    async def publish(
        self, subject: str, payload: bytes | None = None, **kwargs: Any
    ) -> None:
        """Record publish call."""
        self._published.append((subject, payload))

    async def subscribe(self, subject: str, **kwargs: Any) -> Any:
        """Record subscribe call and return a mock subscription."""
        self._subscriptions.append((subject, kwargs))
        return FakeSubscription()

    def is_connected(self) -> bool:
        """Return connected state."""
        return self.connected


class FakeSubscription:
    """Mock subscription handle."""

    async def unsubscribe(self, **kwargs: Any) -> None:
        pass
