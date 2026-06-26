"""deploy/omp/models.yml discovery invariants + mock-gateway catalogue (#1923)."""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_YML = REPO_ROOT / "deploy" / "omp" / "models.yml"


def _load_litellm_provider(path: Path = MODELS_YML) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    return doc["providers"]["litellm"]


def test_models_yml_has_discovery_block() -> None:
    litellm = _load_litellm_provider()
    assert litellm["discovery"]["type"] == "openai-models-list"
    assert "models" not in litellm
    assert litellm["baseUrl"].endswith("/v1")


def test_models_yml_preserves_litellm_provider_shape() -> None:
    litellm = _load_litellm_provider()
    assert litellm["api"] == "openai-completions"
    assert litellm["apiKey"] == "LITELLM_API_KEY"


def test_models_yml_has_no_factory_only_keys() -> None:
    """omp rejects unknown provider keys — factory policy lives elsewhere."""
    litellm = _load_litellm_provider()
    assert "model_policy" not in litellm


class _ModelsListHandler(BaseHTTPRequestHandler):
    catalogue: dict[str, Any] = {
        "object": "list",
        "data": [{"id": "grok-4-fast", "object": "model"}],
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
def mock_gateway() -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsListHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _fetch_catalogue(base_url: str, *, api_key: str = "test-key") -> list[str]:
    req = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode())
    return [entry["id"] for entry in payload.get("data", [])]


def test_discovery_fetch_returns_grok_model(mock_gateway: str, tmp_path: Path) -> None:
    models_yml = tmp_path / "models.yml"
    models_yml.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "litellm": {
                        "baseUrl": mock_gateway,
                        "api": "openai-completions",
                        "apiKey": "LITELLM_API_KEY",
                        "discovery": {"type": "openai-models-list"},
                    }
                }
            },
            sort_keys=False,
        )
    )
    litellm = _load_litellm_provider(models_yml)
    model_ids = _fetch_catalogue(litellm["baseUrl"])
    assert any(model_id.startswith("grok") for model_id in model_ids)


@pytest.mark.integration
@pytest.mark.skipif(
    not __import__("os").environ.get("INTEGRATION"),
    reason="set INTEGRATION=1 to run live LiteLLM gateway catalogue smoke",
)
def test_live_gateway_catalogue_has_grok_model() -> None:
    litellm = _load_litellm_provider()
    api_key = __import__("os").environ.get("LITELLM_API_KEY", "")
    if not api_key:
        pytest.skip("LITELLM_API_KEY unset — live gateway smoke requires proxy auth")
    try:
        model_ids = _fetch_catalogue(litellm["baseUrl"], api_key=api_key)
    except HTTPError as exc:
        pytest.skip(f"live gateway unreachable or rejected auth: {exc}")
    assert any(model_id.startswith("grok") for model_id in model_ids)
