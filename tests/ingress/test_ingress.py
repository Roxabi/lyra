"""Tests for factory-ingress webhook handling."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from factory.ingress.config import ConnectorConfig, IngressConfig
from factory.ingress.normalize import cloudflare_event_kind, github_event_kind
from factory.ingress.publisher import EventPublisher
from factory.ingress.serve import create_app
from factory.ingress.verify import verify_cloudflare_auth, verify_github_signature


def _gh_sig(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def ingress_config() -> IngressConfig:
    return IngressConfig(
        github=ConnectorConfig(enabled=True, webhook_secret="gh-secret"),
        cloudflare=ConnectorConfig(enabled=True, webhook_secret="cf-secret"),
    )


@pytest.fixture
def client(ingress_config: IngressConfig) -> TestClient:
    publisher = AsyncMock(spec=EventPublisher)
    publisher.publish = AsyncMock(return_value=True)
    app = create_app(ingress_config, publisher=publisher)
    with TestClient(app) as tc:
        tc.publisher = publisher  # type: ignore[attr-defined]
        yield tc


def test_verify_github_signature() -> None:
    body = b'{"action":"completed"}'
    assert verify_github_signature(body, _gh_sig(body, "s"), "s")
    assert not verify_github_signature(body, "sha256=deadbeef", "s")


def test_verify_cloudflare_auth() -> None:
    assert verify_cloudflare_auth("cf-secret", "cf-secret")
    assert not verify_cloudflare_auth("wrong", "cf-secret")


def test_github_event_kind() -> None:
    kind = github_event_kind("check_run", {"action": "completed"})
    assert kind == "check_run.completed"


def test_cloudflare_event_kind_failure() -> None:
    payload = {"text": "Pages deployment failed", "name": "pages"}
    assert cloudflare_event_kind(payload) == "pages.deployment.failure"


def test_github_webhook_rejects_bad_signature(client: TestClient) -> None:
    body = json.dumps({"action": "completed"}).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "check_run",
            "X-Hub-Signature-256": "sha256=00",
        },
    )
    assert resp.status_code == 401


def test_github_webhook_accepts_valid(client: TestClient) -> None:
    payload = {
        "action": "completed",
        "repository": {"full_name": "Roxabi/roxabi-factory"},
        "check_run": {"conclusion": "failure", "id": 1},
    }
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "check_run",
            "X-Hub-Signature-256": _gh_sig(body, "gh-secret"),
            "X-GitHub-Delivery": "delivery-123",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["subject_kind"] == "check_run.completed"
    client.publisher.publish.assert_awaited_once()  # type: ignore[attr-defined]


def test_cloudflare_webhook_accepts_valid(client: TestClient) -> None:
    payload = {"text": "deployment succeeded", "name": "pages deploy"}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhook/cloudflare",
        content=body,
        headers={"cf-webhook-auth": "cf-secret"},
    )
    assert resp.status_code == 202
    assert resp.json()["subject_kind"] == "pages.deployment.success"