"""Tests for omp LiteLLM catalogue helpers."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from factory.adapters.omp import _model_catalogue as catalogue


class _ModelsListHandler(BaseHTTPRequestHandler):
    catalogue: dict[str, Any] = {
        "object": "list",
        "data": [
            {"id": "grok-4.20-non-reasoning", "object": "model"},
            {"id": "grok-4.20-reasoning", "object": "model"},
        ],
    }

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/xai/v1/models":
            self.send_error(404)
            return
        body = json.dumps(self.catalogue).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def mock_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsListHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}/xai/v1"
    models_yml = tmp_path / "models.yml"
    models_yml.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "litellm": {
                        "baseUrl": base_url,
                        "api": "openai-completions",
                        "apiKey": "LITELLM_API_KEY",
                        "discovery": {"type": "openai-models-list"},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LITELLM_API_KEY", "test-key")
    catalogue._load_litellm_provider.cache_clear()
    try:
        yield
    finally:
        server.shutdown()
        thread.join(timeout=5)
        catalogue._load_litellm_provider.cache_clear()


def test_first_registry_model_returns_catalogue_head(mock_gateway: None) -> None:
    assert catalogue.first_registry_model() == "grok-4.20-non-reasoning"
