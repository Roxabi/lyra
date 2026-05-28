"""Minimal fake store for testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeStore:
    """Generic fake store for testing.

    Satisfies the basic lifecycle contract (connect/close) and records
    all get/set calls.  Use domain-specific fakes when store protocol
    methods matter.
    """

    connected: bool = False
    raise_on_connect: Exception | None = None
    raise_on_close: Exception | None = None
    _data: dict[str, Any] = field(default_factory=dict)
    _calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list
    )

    async def connect(self) -> None:
        """Record connect and optionally raise."""
        if self.raise_on_connect:
            raise self.raise_on_connect
        self.connected = True

    async def close(self) -> None:
        """Record close and optionally raise."""
        if self.raise_on_close:
            raise self.raise_on_close
        self.connected = False

    def get(self, key: str) -> Any | None:
        """Return stored value or None."""
        self._calls.append(("get", (key,), {}))
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        """Store value."""
        self._calls.append(("set", (key, value), {}))
        self._data[key] = value

    def record_call(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Helper to record arbitrary method invocations."""
        self._calls.append((name, args, kwargs))
