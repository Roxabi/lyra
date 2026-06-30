"""Parametrized ingress pipeline acceptance matrix (ADR-096 AC-S)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tests.ingress.conftest import PipelineHarness, _FakeConnector, gh_sig

from factory.infrastructure.stores.ingress.installation_store import InstallationStore
from factory.ingress.config import ConnectorConfig, IngressConfig
from factory.ingress.metrics import unknown_installation_total
from factory.ingress.orchestrator import MAX_BODY_BYTES
from factory.ingress.ports import VerificationError
from factory.ingress.publisher import EventPublisher
from factory.ingress.serve import create_app
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.event import LyraEvent


def _post_github(
    client: TestClient, body: bytes, *, signature: str
) -> object:
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "check_run",
            "X-Hub-Signature-256": signature,
        },
    )


def _post_cf(
    client: TestClient, body: bytes, path: str, *, auth: str = "cf-secret"
) -> object:
    return client.post(
        f"/webhook/cloudflare{path}",
        content=body,
        headers={"cf-webhook-auth": auth},
    )


async def _case_github_bad_sig(client: TestClient, store: InstallationStore) -> object:
    body = json.dumps({"action": "completed"}).encode()
    return _post_github(client, body, signature="sha256=00")


async def _case_github_unknown_install(
    client: TestClient, store: InstallationStore
) -> object:
    payload = {
        "action": "completed",
        "installation": {"id": 99999},
        "check_run": {"conclusion": "success", "id": 1},
    }
    body = json.dumps(payload).encode()
    return _post_github(client, body, signature=gh_sig(body, "gh-secret"))


async def _case_github_disabled_row(
    client: TestClient, store: InstallationStore
) -> object:
    await store.upsert_lifecycle("github", "5555", "default", enabled=False)
    payload = {
        "action": "completed",
        "installation": {"id": 5555},
        "check_run": {"conclusion": "success", "id": 1},
    }
    body = json.dumps(payload).encode()
    return _post_github(client, body, signature=gh_sig(body, "gh-secret"))


async def _case_github_invalid_registry_slug(
    client: TestClient, store: InstallationStore
) -> object:
    db = store._require_db()
    await db.execute(
        "INSERT INTO connector_installations ("
        "connector, external_id, factory_tenant, enabled, "
        "metadata_json, updated_at) "
        "VALUES (?, ?, ?, 1, NULL, ?)",
        ("github", "7777", "BadTenant", "2026-01-01T00:00:00+00:00"),
    )
    await db.commit()
    payload = {
        "action": "completed",
        "installation": {"id": 7777},
        "check_run": {"conclusion": "success", "id": 1},
    }
    body = json.dumps(payload).encode()
    return _post_github(client, body, signature=gh_sig(body, "gh-secret"))


async def _case_github_body_too_large(
    client: TestClient, store: InstallationStore
) -> object:
    body = b"x" * (MAX_BODY_BYTES + 1)
    return _post_github(client, body, signature=gh_sig(body, "gh-secret"))


async def _case_github_disabled_config(
    client: TestClient, store: InstallationStore
) -> object:
    config = IngressConfig(
        connectors={
            "github": ConnectorConfig(enabled=False, webhook_secret="gh-secret"),
            "cloudflare": ConnectorConfig(enabled=True, webhook_secret="cf-secret"),
        }
    )
    publisher = AsyncMock(spec=EventPublisher)
    body = json.dumps({"action": "completed"}).encode()
    with TestClient(
        create_app(config, publisher=publisher, installations=store)
    ) as alt:
        return _post_github(alt, body, signature=gh_sig(body, "gh-secret"))


async def _case_cf_bad_path_slug(
    client: TestClient, store: InstallationStore
) -> object:
    body = json.dumps({"text": "ok", "name": "pages"}).encode()
    return _post_cf(client, body, "/InvalidSlug")


async def _case_cf_idor_path_mismatch(
    client: TestClient, store: InstallationStore
) -> object:
    await store.seed("cloudflare", "acct-1", "acme")
    payload = {
        "text": "deployment succeeded",
        "name": "pages",
        "account_id": "acct-1",
    }
    body = json.dumps(payload).encode()
    return _post_cf(client, body, "/default")


async def _case_cf_alias_no_registry(
    client: TestClient, store: InstallationStore
) -> object:
    body = json.dumps(
        {"text": "deployment succeeded", "name": "pages deploy"}
    ).encode()
    return _post_cf(client, body, "")


async def _case_cf_publish_resolved(
    client: TestClient, store: InstallationStore
) -> object:
    await store.seed("cloudflare", "acct-9", "default")
    payload = {
        "text": "deployment succeeded",
        "name": "pages deploy",
        "account_id": "acct-9",
    }
    body = json.dumps(payload).encode()
    return _post_cf(client, body, "/default")


_ROUTE_CASES: dict[str, Callable] = {
    "github_bad_sig": _case_github_bad_sig,
    "github_unknown_install": _case_github_unknown_install,
    "github_disabled_row": _case_github_disabled_row,
    "github_invalid_registry_slug": _case_github_invalid_registry_slug,
    "github_body_too_large": _case_github_body_too_large,
    "github_disabled_config": _case_github_disabled_config,
    "cf_bad_path_slug": _case_cf_bad_path_slug,
    "cf_idor_path_mismatch": _case_cf_idor_path_mismatch,
    "cf_alias_no_registry": _case_cf_alias_no_registry,
    "cf_publish_resolved": _case_cf_publish_resolved,
}


@pytest.mark.parametrize(
    ("case", "status", "publish", "metric_connector"),
    [
        ("github_bad_sig", 401, False, None),
        ("github_unknown_install", 202, False, "github"),
        ("github_disabled_row", 202, False, "github"),
        ("github_invalid_registry_slug", 202, False, None),
        ("github_body_too_large", 413, False, None),
        ("github_disabled_config", 503, False, None),
        ("cf_bad_path_slug", 401, False, None),
        ("cf_idor_path_mismatch", 202, False, None),
        ("cf_alias_no_registry", 202, False, "cloudflare"),
        ("cf_publish_resolved", 202, True, None),
    ],
)
async def test_route_pipeline_matrix(
    client: TestClient,
    case: str,
    status: int,
    publish: bool,
    metric_connector: str | None,
) -> None:
    store: InstallationStore = client.store  # type: ignore[attr-defined]
    handler = _ROUTE_CASES[case]
    resp = await handler(client, store)
    assert resp.status_code == status
    if publish:
        client.publisher.publish.assert_awaited()  # type: ignore[attr-defined]
        if case == "cf_publish_resolved":
            event = client.publisher.publish.await_args.args[0]  # type: ignore[attr-defined]
            assert event.tenant == "default"
    elif case != "github_disabled_config":
        client.publisher.publish.assert_not_awaited()  # type: ignore[attr-defined]
    if metric_connector is not None:
        assert unknown_installation_total(metric_connector) >= 1


async def test_handle_webhook_verify_before_resolve(
    fake_pipeline: PipelineHarness, fake_connector: _FakeConnector
) -> None:
    call_order: list[str] = []

    def _verify(*_a: object, **_k: object) -> None:
        call_order.append("verify")

    async def _resolve(*_a: object, **_k: object) -> str:
        call_order.append("resolve")
        return "default"

    fake_pipeline.installations.resolve = AsyncMock(side_effect=_resolve)  # type: ignore[attr-defined]
    fake_connector.verify = MagicMock(side_effect=_verify)

    await fake_pipeline.call("fake", headers={}, body=b'{"ok": true}')
    assert call_order == ["verify", "resolve"]


async def test_handle_webhook_verify_failure_skips_resolve(
    fake_pipeline: PipelineHarness, fake_connector: _FakeConnector
) -> None:
    fake_connector.verify = MagicMock(side_effect=VerificationError("bad"))
    with pytest.raises(HTTPException) as exc:
        await fake_pipeline.call("fake", headers={}, body=b"{}")
    assert exc.value.status_code == 401
    fake_pipeline.installations.resolve.assert_not_awaited()  # type: ignore[attr-defined]


async def test_handle_webhook_per_account_unknown_external_drops(
    per_account_pipeline: PipelineHarness,
) -> None:
    resp = await per_account_pipeline.call(
        "cf",
        headers={},
        body=b'{"account_id": "missing"}',
        path_tenant="default",
    )
    assert resp.status_code == 202
    assert json.loads(bytes(resp.body)) == {"accepted": True}
    per_account_pipeline.publisher.publish.assert_not_awaited()


async def test_handle_webhook_normalize_validation_error_drops(
    fake_pipeline: PipelineHarness, fake_connector: _FakeConnector
) -> None:
    def _normalize_raises(*_a: object, **_k: object) -> LyraEvent:
        return LyraEvent.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "trace_id": "t",
                "issued_at": datetime.now(tz=UTC).isoformat(),
                "service": "FAKE",
                "kind": "bad",
                "level": "info",
                "tenant": "INVALID",
            }
        )

    fake_connector.normalize = MagicMock(side_effect=_normalize_raises)
    resp = await fake_pipeline.call("fake", headers={}, body=b"{}")
    assert resp.status_code == 202
    assert json.loads(bytes(resp.body)) == {"accepted": True}
    fake_pipeline.publisher.publish.assert_not_awaited()


async def test_installation_store_resolve_rules(tmp_path) -> None:
    store = InstallationStore(tmp_path / "ingress.db")
    await store.connect()
    await store.seed("github", "12345", "default")
    assert await store.resolve("github", "12345") == "default"
    assert await store.resolve("github", "99999") is None
    assert await store.resolve("github", None) == "default"
    with pytest.raises(ValueError, match="wildcard"):
        await store.seed("github", "*", "default")
    with pytest.raises(VerificationError):
        await store.seed("github", "1", "BadTenant")
    await store.upsert_lifecycle("github", "off-1", "default", enabled=False)
    assert await store.resolve("github", "off-1") is None
    assert await store.resolve("cloudflare", None) is None
    await store.close()