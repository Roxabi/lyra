from __future__ import annotations

from .base import LlmProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, LlmProvider] = {}

    def register(self, backend: str, driver: LlmProvider) -> None:
        self._drivers[backend] = driver

    def get(self, backend: str) -> LlmProvider:
        if backend not in self._drivers:
            raise KeyError(
                f"No provider for {backend!r}. Registered: {sorted(self._drivers)}"
            )
        return self._drivers[backend]
