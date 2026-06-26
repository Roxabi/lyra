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
            {"id": "deepseek-v4-pro", "object": "model"},
            {"id": "grok-4.20-non-reasoning", "object": "model"},
            {"id": "grok-4.20-reasoning", "object": "model"},
        ],
    }

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/models":
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
    base_url = f"http://{host}:{port}/v1"
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
    policy_yml = tmp_path / "factory-model-policy.yml"
    policy_yml.write_text(
        yaml.safe_dump(
            {
                "boot": {"select": "first"},
                "unavailable": {
                    "select": "first",
                    "skip_requested": True,
                    "filter": {"exclude_contains": ["reasoning"]},
                },
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


def test_resolve_boot_model_uses_first_catalogue_id(mock_gateway: None) -> None:
    assert catalogue.resolve_boot_model() == "deepseek-v4-pro"


def test_resolve_fallback_skips_requested_and_excludes_reasoning(
    mock_gateway: None,
) -> None:
    assert catalogue.resolve_fallback_model(requested="grok-4.20-non-reasoning") == (
        "deepseek-v4-pro"
    )


def test_resolve_boot_model_raises_when_catalogue_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_yml = tmp_path / "models.yml"
    models_yml.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "litellm": {
                        "baseUrl": "http://127.0.0.1:9/v1",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "factory-model-policy.yml").write_text(
        yaml.safe_dump({"boot": {"select": "first"}}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    catalogue._load_litellm_provider.cache_clear()
    try:
        with pytest.raises(catalogue.ModelCatalogueError):
            catalogue.resolve_boot_model()
    finally:
        catalogue._load_litellm_provider.cache_clear()


def test_select_max_lex_from_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_yml = tmp_path / "models.yml"
    models_yml.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "litellm": {
                        "baseUrl": "http://127.0.0.1:9/v1",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "factory-model-policy.yml").write_text(
        yaml.safe_dump(
            {
                "boot": {
                    "select": "max_lex",
                    "filter": {"include_prefix": ["grok"]},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    catalogue._load_litellm_provider.cache_clear()
    try:
        picked = catalogue._select_from_catalogue(
            ["grok-4.20-reasoning", "grok-4.20-non-reasoning"],
            catalogue.load_model_policy()["boot"],
        )
        assert picked == "grok-4.20-reasoning"
    finally:
        catalogue._load_litellm_provider.cache_clear()
