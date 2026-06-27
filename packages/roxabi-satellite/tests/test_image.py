"""Tests for roxabi_satellite.image."""

from __future__ import annotations

import json

from roxabi_satellite.image.delivery import sanitize_delivery_exception
from roxabi_satellite.image.errors import image_worker_error_from_legacy
from roxabi_satellite.image.replies import build_image_error_reply


def test_image_worker_error_from_legacy_maps_delivery_failed() -> None:
    we = image_worker_error_from_legacy("delivery_failed", "BlobStore down")
    assert we.code == "worker.internal"
    assert we.retryable is True
    assert we.detail == "BlobStore down"


def test_build_image_error_reply_populates_worker_error() -> None:
    raw = build_image_error_reply(
        trace_id="trace-1",
        request_id="req-1",
        error="unknown_engine",
        error_detail="flux99",
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["error"] == "unknown_engine"
    assert data["worker_error"]["code"] == "worker.validation"


def test_sanitize_delivery_exception_without_httpx() -> None:
    assert sanitize_delivery_exception(RuntimeError("boom")) == "internal delivery error"  # noqa: E501


def test_sanitize_delivery_exception_http_status() -> None:
    import httpx

    request = httpx.Request("PUT", "https://example.com/blobs")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("fail", request=request, response=response)
    assert sanitize_delivery_exception(exc) == "upstream HTTP 503"


def test_sanitize_delivery_exception_timeout() -> None:
    import httpx

    assert sanitize_delivery_exception(httpx.TimeoutException("slow")) == (
        "BlobStore request timed out"
    )