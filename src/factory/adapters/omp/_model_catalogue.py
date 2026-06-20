"""LiteLLM catalogue helpers for omp model fallback (#1923)."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

log = logging.getLogger(__name__)

_ENV_AGENT_DIR = "PI_CODING_AGENT_DIR"
_DEFAULT_AGENT_DIR = Path("/home/factory/.config/omp-pi")
_REPO_ROOT = Path(__file__).resolve().parents[4]
_REPO_MODELS_YML = _REPO_ROOT / "deploy" / "omp" / "models.yml"


def _models_yml_path() -> Path:
    agent_dir = os.environ.get(_ENV_AGENT_DIR, "").strip()
    if agent_dir:
        candidate = Path(agent_dir) / "models.yml"
        if candidate.is_file():
            return candidate
    default_candidate = _DEFAULT_AGENT_DIR / "models.yml"
    if default_candidate.is_file():
        return default_candidate
    return _REPO_MODELS_YML


def _litellm_api_key() -> str:
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("LITELLM_API_KEY_FILE", "").strip()
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    return ""


@lru_cache(maxsize=1)
def _load_litellm_provider() -> dict[str, Any] | None:
    path = _models_yml_path()
    if not path.is_file():
        log.warning("omp model catalogue: models.yml not found at %s", path)
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("omp model catalogue: failed to read %s: %s", path, exc)
        return None
    provider = (doc or {}).get("providers", {}).get("litellm")
    return provider if isinstance(provider, dict) else None


def fetch_catalogue_model_ids() -> list[str]:
    """Return model ids from the LiteLLM OpenAI-compatible catalogue."""
    provider = _load_litellm_provider()
    if provider is None:
        return []
    base_url = str(provider.get("baseUrl", "")).rstrip("/")
    if not base_url:
        return []
    api_key = _litellm_api_key()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    req = Request(f"{base_url}/models", headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode())
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("omp model catalogue: fetch failed for %s: %s", base_url, exc)
        return []
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for entry in data:
        if isinstance(entry, dict):
            model_id = entry.get("id")
            if isinstance(model_id, str) and model_id:
                ids.append(model_id)
    return ids


def first_registry_model() -> str | None:
    """First model id from the discovered LiteLLM catalogue, if any."""
    ids = fetch_catalogue_model_ids()
    return ids[0] if ids else None