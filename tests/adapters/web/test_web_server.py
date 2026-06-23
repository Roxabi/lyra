"""HTTP tests for the web smoke FastAPI app."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.adapters.web.web_adapter import WebAdapter
from factory.adapters.web.web_server import create_app


@pytest.fixture
def client() -> tuple[TestClient, MagicMock]:
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
    return TestClient(app), bus


class TestWebServer:
    def test_list_agents(self, client: tuple[TestClient, MagicMock]) -> None:
        tc, _ = client
        res = tc.get("/api/agents")
        assert res.status_code == 200
        assert res.json()["agents"] == ["alpha", "beta"]

    def test_post_chat_accepts_message(
        self, client: tuple[TestClient, MagicMock]
    ) -> None:
        tc, bus = client
        res = tc.post(
            "/api/chat",
            json={"agent": "alpha", "text": "hello"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["accepted"] is True
        assert body["session_id"]
        # The message must reach the dispatch stage — proves the inbound pipeline
        # ran end-to-end and the Router did not DROP the WebMeta message.
        assert bus.put.await_count == 1

    def test_post_chat_rejects_unknown_agent(
        self, client: tuple[TestClient, MagicMock]
    ) -> None:
        tc, bus = client
        res = tc.post(
            "/api/chat",
            json={"agent": "nope", "text": "hello"},
        )
        assert res.status_code == 400
        # Invalid input is rejected before the pipeline runs.
        assert bus.put.await_count == 0
