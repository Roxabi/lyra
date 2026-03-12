from __future__ import annotations

import logging

from .base import LlmProvider

log = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, LlmProvider] = {}

    def register(self, backend: str, driver: LlmProvider) -> None:
        self._drivers[backend] = driver

    def get(self, backend: str) -> LlmProvider:
        if backend not in self._drivers:
            log.debug(
                "Unknown backend %r. Registered: %s", backend, sorted(self._drivers)
            )
            raise KeyError(f"Unknown backend: {backend!r}")
        return self._drivers[backend]
