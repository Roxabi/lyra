"""Route smoke tests for factory-ingress aliases (ADR-096)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from tests.ingress.conftest import gh_sig

from factory.ingress.normalize import cloudflare_event_kind, github_event_kind
from factory.ingress.verify import verify_cloudflare_auth, verify_github_signature
from roxabi_contracts.event import per_connector_tenant_event


def test_verify_github_signature() -> None:
    body = b'{"action":"completed"}'
    assert verify_github_signature(body, gh_sig(body, "s"), "s")
    assert not verify_github_signature(body, "sha256=deadbeef", "s")


def test_verify_cloudflare_auth() -> None:
    assert verify_cloudflare_auth("cf-secret", "cf-secret")
    assert not verify_cloudflare_auth("wrong", "cf-secret")


def test_per_connector_tenant_subject() -> None:
    assert (
        per_connector_tenant_event("github", "default", "check_run.completed")
        == "factory.event.github.default.check_run.completed"
    )


def test_github_event_kind() -> None:
    kind = github_event_kind("check_run", {"action": "completed"})
    assert kind == "check_run.completed"


def test_cloudflare_event_kind_failure() -> None:
    payload = {"text": "Pages deployment failed", "name": "pages"}
    assert cloudflare_event_kind(payload) == "pages.deployment.failure"


def test_github_alias_smoke_publishes(client: TestClient) -> None:
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
            "X-Hub-Signature-256": gh_sig(body, "gh-secret"),
            "X-GitHub-Delivery": "delivery-123",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["subject_kind"] == "check_run.completed"
    client.publisher.publish.assert_awaited_once()  # type: ignore[attr-defined]
    event = client.publisher.publish.await_args.args[0]  # type: ignore[attr-defined]
    assert event.tenant == "default"


def test_cloudflare_alias_smoke_returns_202(client: TestClient) -> None:
    body = json.dumps({"text": "deployment succeeded", "name": "pages deploy"}).encode()
    resp = client.post(
        "/webhook/cloudflare",
        content=body,
        headers={"cf-webhook-auth": "cf-secret"},
    )
    assert resp.status_code == 202
    assert "accepted" in resp.json()