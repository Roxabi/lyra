"""HTTP tests for the web smoke FastAPI app."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app


@pytest.fixture
def client() -> TestClient:
    bus = MagicMock()
    bus.put = AsyncMock()
    adapter = WebAdapter(
        inbound_bus=bus,
        agent_names=["alpha", "beta"],
        port=19999,
    )
    listener = MagicMock()
    listener.cache_inbound = MagicMock()
    adapter._outbound_listener = listener
    app = create_app(adapter)
    return TestClient(app)


class TestWebServer:
    def test_list_agents(self, client: TestClient) -> None:
        res = client.get("/api/agents")
        assert res.status_code == 200
        assert res.json()["agents"] == ["alpha", "beta"]

    def test_post_chat_accepts_message(self, client: TestClient) -> None:
        res = client.post(
            "/api/chat",
            json={"agent": "alpha", "text": "hello"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["accepted"] is True
        assert body["session_id"]

    def test_post_chat_rejects_unknown_agent(self, client: TestClient) -> None:
        res = client.post(
            "/api/chat",
            json={"agent": "nope", "text": "hello"},
        )
        assert res.status_code == 400