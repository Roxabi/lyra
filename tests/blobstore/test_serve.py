"""RED-phase tests for factory.blobstore.serve — skeleton boot (N5, N6, S1 auth)."""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import nats.errors
import pytest
from fastapi.testclient import TestClient

from factory.blobstore.serve import (
    _blob_count,
    _connect_nats,
    _disk_used_pct,
    _provision_nats,
    build_app,
)

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: pathlib.Path):  # type: ignore[return]
    """Synchronous TestClient for build_app with a fixed test token."""
    tok = tmp_path / "blobstore.tok"
    tok.write_text("test-token")
    blob_root = tmp_path / "blobs"
    blob_root.mkdir()
    app = build_app(token_path=tok, blob_root=blob_root)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# N5 — GET /healthz (no auth required)
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthz_returns_200_with_full_body(self, client: TestClient) -> None:
        """GET /healthz returns 200 with status, disk_used_pct, and blob_count."""
        # Arrange — no headers required (N5: Auth = none)
        # Act
        response = client.get("/healthz")
        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # SC-Obs-1: disk_used_pct + blob_count required (None acceptable on failure)
        assert "disk_used_pct" in body
        assert "blob_count" in body
        if body["disk_used_pct"] is not None:
            assert isinstance(body["disk_used_pct"], (int, float))
            assert 0.0 <= float(body["disk_used_pct"]) <= 100.0
        if body["blob_count"] is not None:
            assert isinstance(body["blob_count"], int)
            assert body["blob_count"] >= 0


# ---------------------------------------------------------------------------
# N6 — GET /metrics (no auth required)
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_returns_200_with_disk_used_pct_gauge(
        self, client: TestClient
    ) -> None:
        """GET /metrics returns 200 with blobstore_up, disk_used_pct, blob_count."""
        # Arrange — no headers required (N6: Auth = none)
        # Act
        response = client.get("/metrics")
        # Assert
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("text/plain")
        body = response.text
        # SC-Obs-2: required gauges
        assert "blobstore_up" in body
        assert "blobstore_disk_used_pct" in body
        assert "blobstore_blob_count" in body


# ---------------------------------------------------------------------------
# S1 — BearerAuthMiddleware fires on protected paths
# ---------------------------------------------------------------------------


class TestBearerAuth:
    def test_protected_endpoint_returns_401_without_bearer(
        self, client: TestClient
    ) -> None:
        """GET /blobs/<store_key> returns 401 when Authorization header is absent."""
        # Arrange — no Authorization header
        # Act
        response = client.get("/blobs/anything-store-key")
        # Assert
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Narrowed boundary catches — failure paths return None / degraded mode
# ---------------------------------------------------------------------------


class TestServeFailurePaths:
    @pytest.mark.anyio
    async def test_disk_used_pct_oserror_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        def _boom(_path: pathlib.Path) -> None:
            raise OSError("disk unavailable")

        monkeypatch.setattr("factory.blobstore.serve.shutil.disk_usage", _boom)

        assert await _disk_used_pct(tmp_path) is None

    @pytest.mark.anyio
    async def test_blob_count_aiosqlite_error_returns_none(self) -> None:
        app = MagicMock()
        store = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=aiosqlite.Error("query failed"))
        store._conn = conn
        app.state.store = store

        assert await _blob_count(app) is None

    @pytest.mark.anyio
    async def test_connect_nats_missing_url_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NATS_URL", raising=False)

        assert await _connect_nats() is None

    @pytest.mark.anyio
    async def test_connect_nats_error_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setattr(
            "roxabi_nats.nats_connect",
            AsyncMock(side_effect=nats.errors.Error("connect refused")),
        )

        assert await _connect_nats() is None

    @pytest.mark.anyio
    async def test_provision_nats_kv_error_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nc = MagicMock()
        js = MagicMock()
        kv = MagicMock()
        kv.put = AsyncMock(side_effect=nats.errors.Error("kv put failed"))
        nc.jetstream.return_value = js
        js.key_value = AsyncMock(return_value=kv)

        sink = MagicMock()
        sink._degraded = False
        sink.provision = AsyncMock()
        monkeypatch.setattr(
            "factory.blobstore.serve.BlobAuditSink",
            lambda: sink,
        )

        app = MagicMock()
        app.state = MagicMock()

        await _provision_nats(app, nc)  # must not raise

        sink.provision.assert_awaited_once_with(nc)
        kv.put.assert_awaited_once_with("blobstore.ready", b"true")
        assert app.state.nats_provisioned is True
