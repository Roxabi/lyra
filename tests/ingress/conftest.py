"""Shared fixtures for ingress acceptance tests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.infrastructure.stores.ingress.installation_store import InstallationStore
from factory.ingress.config import ConnectorConfig, IngressConfig
from factory.ingress.connectors.registry import ConnectorRegistry, default_registry
from factory.ingress.metrics import reset_metrics
from factory.ingress.orchestrator import handle_webhook
from factory.ingress.ports import (
    InstallationRegistry,
    SecretResolver,
)
from factory.ingress.publisher import EventPublisher
from factory.ingress.secrets import PodmanSecretResolver
from factory.ingress.serve import create_app
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.event import LyraEvent


def gh_sig(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def ingress_config() -> IngressConfig:
    return IngressConfig(
        connectors={
            "github": ConnectorConfig(enabled=True, webhook_secret="gh-secret"),
            "cloudflare": ConnectorConfig(enabled=True, webhook_secret="cf-secret"),
        }
    )


@pytest.fixture
def installation_store(tmp_path) -> Iterator[InstallationStore]:
    store = InstallationStore(tmp_path / "ingress.db")
    asyncio.run(store.connect())
    yield store
    asyncio.run(store.close())


@pytest.fixture
def client(
    ingress_config: IngressConfig, installation_store: InstallationStore
) -> Iterator[TestClient]:
    reset_metrics()
    publisher = AsyncMock(spec=EventPublisher)
    publisher.publish = AsyncMock(return_value=True)
    app = create_app(
        ingress_config,
        publisher=publisher,
        installations=installation_store,
    )
    with TestClient(app) as tc:
        tc.publisher = publisher  # type: ignore[attr-defined]
        tc.store = installation_store  # type: ignore[attr-defined]
        yield tc


@pytest.fixture
def publisher() -> AsyncMock:
    pub = AsyncMock(spec=EventPublisher)
    pub.publish = AsyncMock(return_value=True)
    return pub


@pytest.fixture
def pipeline(
    ingress_config: IngressConfig,
    installation_store: InstallationStore,
    publisher: AsyncMock,
) -> PipelineHarness:
    return PipelineHarness(
        config=ingress_config,
        registry=default_registry(),
        installations=installation_store,
        secrets=PodmanSecretResolver(ingress_config),
        publisher=publisher,
    )


@dataclass
class PipelineHarness:
    config: IngressConfig
    registry: ConnectorRegistry
    installations: InstallationRegistry
    secrets: SecretResolver
    publisher: AsyncMock

    async def call(
        self,
        connector: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        path_tenant: str | None = None,
    ):
        reset_metrics()
        return await handle_webhook(
            connector,
            headers=headers,
            body=body,
            path_tenant=path_tenant,
            config=self.config,
            registry=self.registry,
            installations=self.installations,
            secrets=self.secrets,
            publisher=self.publisher,
        )


class _FakeConnector:
    name = "fake"
    family: Literal["centralized_app", "per_account"] = "centralized_app"

    def __init__(self) -> None:
        self.verify = MagicMock()
        self.parse_external_id = MagicMock(return_value="ext-1")
        self.normalize = MagicMock(
            return_value=LyraEvent(
                contract_version=CONTRACT_VERSION,
                trace_id="t",
                issued_at=datetime.now(tz=UTC),
                service="fake",
                kind="test.event",
                level="info",
                tenant="default",
            )
        )

    async def apply_lifecycle(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        registry: InstallationRegistry,
    ) -> None:
        return


class _PerAccountConnector(_FakeConnector):
    name = "cf"
    family: Literal["centralized_app", "per_account"] = "per_account"


@pytest.fixture
def fake_pipeline(publisher: AsyncMock) -> PipelineHarness:
    connector = _FakeConnector()
    config = IngressConfig(
        connectors={"fake": ConnectorConfig(enabled=True, webhook_secret="s")}
    )
    installations = AsyncMock()
    installations.resolve = AsyncMock(return_value="default")
    return PipelineHarness(
        config=config,
        registry=ConnectorRegistry({"fake": connector}),
        installations=installations,
        secrets=_FakeSecrets(),
        publisher=publisher,
    )


@pytest.fixture
def fake_connector(fake_pipeline: PipelineHarness) -> _FakeConnector:
    connector = fake_pipeline.registry.get("fake")
    assert isinstance(connector, _FakeConnector)
    return connector


class _FakeSecrets(SecretResolver):
    def get_connector_secret(self, connector: str) -> str:
        return "secret"

    def get_tenant_secret(self, connector: str, factory_tenant: str) -> str | None:
        return "secret"


@pytest.fixture
def per_account_pipeline(publisher: AsyncMock) -> PipelineHarness:
    config = IngressConfig(
        connectors={"cf": ConnectorConfig(enabled=True, webhook_secret="s")}
    )
    installations = AsyncMock()
    installations.resolve = AsyncMock(return_value=None)
    return PipelineHarness(
        config=config,
        registry=ConnectorRegistry({"cf": _PerAccountConnector()}),
        installations=installations,
        secrets=_FakeSecrets(),
        publisher=publisher,
    )