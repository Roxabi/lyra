"""Public /healthz — HealthCmd must not use auth-gated /api/agents (Slice 3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app


def test_healthz_public_no_auth() -> None:
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19996)
    adapter._outbound_listener = MagicMock()
    app = create_app(adapter, control_plane=None)
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_api_agents_still_requires_auth() -> None:
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(inbound_bus=bus, agent_names=["alpha"], port=19996)
    adapter._outbound_listener = MagicMock()
    app = create_app(adapter, control_plane=None)
    client = TestClient(app)
    res = client.get("/api/agents")
    assert res.status_code == 401


def test_dashboard_healthcmd_uses_healthz_not_agents() -> None:
    path = Path("deploy/quadlet/factory-dashboard.container")
    text = path.read_text(encoding="utf-8")
    assert "/healthz" in text
    assert "/api/agents" not in text.split("HealthCmd=")[1].split("\n")[0]
