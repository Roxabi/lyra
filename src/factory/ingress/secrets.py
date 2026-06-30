"""Secret resolution for ingress connectors."""

from __future__ import annotations

from factory.ingress.config import IngressConfig
from factory.ingress.ports import VerificationError


class PodmanSecretResolver:
    """Resolve connector secrets from loaded ingress config."""

    def __init__(self, config: IngressConfig) -> None:
        self._config = config

    def get_connector_secret(self, connector: str) -> str:
        entry = self._config.connectors.get(connector)
        if entry is None or not entry.enabled or not entry.webhook_secret:
            raise VerificationError("invalid auth")
        return entry.webhook_secret

    def get_tenant_secret(self, connector: str, factory_tenant: str) -> str | None:
        entry = self._config.connectors.get(connector)
        if entry is None or not entry.enabled or not entry.webhook_secret:
            return None
        # V1: per-tenant dynamic secrets deferred; default tenant uses connector secret.
        if factory_tenant == "default":
            return entry.webhook_secret
        return None