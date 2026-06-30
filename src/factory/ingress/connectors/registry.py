"""Built-in connector registration."""

from __future__ import annotations

from factory.ingress.connectors.cloudflare import CloudflareConnector
from factory.ingress.connectors.github import GitHubConnector
from factory.ingress.ports import Connector

_BUILTIN: dict[str, Connector] = {
    "github": GitHubConnector(),
    "cloudflare": CloudflareConnector(),
}


class ConnectorRegistry:
    def __init__(self, connectors: dict[str, Connector] | None = None) -> None:
        self._connectors = connectors or dict(_BUILTIN)

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._connectors)


def default_registry() -> ConnectorRegistry:
    return ConnectorRegistry()