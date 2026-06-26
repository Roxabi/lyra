"""Tests for roxabi_satellite.socialmedia."""

from __future__ import annotations

from roxabi_contracts.errors import WorkerError
from roxabi_contracts.socialmedia.models import SocialMediaPublishRequest
from roxabi_satellite.socialmedia.errors import worker_error_from_http_provider
from roxabi_satellite.socialmedia.replies import build_publish_error
from roxabi_satellite.socialmedia.validation import validate_publish_request


def _publish_payload(**overrides: object) -> dict:
    base = {
        "request_id": "req-1",
        "brand_slug": "roxabi",
        "content": "hello",
        "platforms": ["x"],
    }
    base.update(overrides)
    return base


def test_validate_publish_request_ok() -> None:
    outcome = validate_publish_request(_publish_payload())
    assert outcome.error is None
    assert isinstance(outcome.request, SocialMediaPublishRequest)


def test_validate_publish_request_malformed() -> None:
    outcome = validate_publish_request(_publish_payload(platforms=[]))
    assert outcome.request is None
    assert outcome.error


def test_worker_error_from_http_provider_maps_auth_and_rate_limit() -> None:
    auth = worker_error_from_http_provider(401, "unauthorized")
    assert auth is not None
    assert auth.code == "provider.auth"
    assert auth.retryable is False

    rate = worker_error_from_http_provider(429, "slow down")
    assert rate is not None
    assert rate.code == "provider.rate_limit"
    assert rate.retryable is True

    assert worker_error_from_http_provider(500, "boom") is None


def test_build_publish_error_carries_worker_error_and_validation_errors() -> None:
    req = SocialMediaPublishRequest.model_validate(
        {
            "contract_version": "0.11",
            "trace_id": "trace-1",
            "issued_at": "2026-06-26T12:00:00Z",
            "job_id": "job-1",
            "request_id": "req-1",
            "brand_slug": "roxabi",
            "content": "hello",
            "platforms": ["x"],
        }
    )
    we = WorkerError(code="provider.auth", message="nope", retryable=False)
    resp = build_publish_error(
        req,
        error="nope",
        worker_error=we,
        provider_body={"provider": "x", "error": "bad token"},
    )
    assert resp.ok is False
    assert resp.worker_error == we
    assert resp.validation_errors == [{"provider": "x", "error": "bad token"}]