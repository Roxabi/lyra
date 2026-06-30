"""Ingress protocol boundaries (ADR-096)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from roxabi_contracts.event import LyraEvent


class VerificationError(Exception):
    """Webhook signature or auth header validation failed."""


@runtime_checkable
class SecretResolver(Protocol):
    def get_connector_secret(self, connector: str) -> str: ...
    def get_tenant_secret(self, connector: str, factory_tenant: str) -> str | None: ...


@runtime_checkable
class InstallationRegistry(Protocol):
    async def resolve(self, connector: str, external_id: str | None) -> str | None: ...
    async def upsert_lifecycle(
        self,
        connector: str,
        external_id: str,
        factory_tenant: str,
        *,
        enabled: bool,
    ) -> None: ...


@runtime_checkable
class Connector(Protocol):
    name: str
    family: Literal["centralized_app", "per_account"]

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secrets: SecretResolver,
        *,
        path_tenant: str | None,
    ) -> None: ...

    def parse_external_id(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        path_tenant: str | None,
    ) -> str | None: ...

    async def apply_lifecycle(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        registry: InstallationRegistry,
    ) -> None: ...

    def normalize(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        tenant: str,
    ) -> LyraEvent: ...