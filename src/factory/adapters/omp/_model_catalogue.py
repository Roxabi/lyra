"""LiteLLM catalogue helpers for omp model boot + fallback (#1923).

Boot and fallback policies live in ``deploy/omp/models.yml`` under
``providers.litellm.model_policy`` — no hardcoded model ids or provider
heuristics in Python. The catalogue source of truth is always
``GET {baseUrl}/models`` (same merged list Lyra/llmCLI publishes).
"""

from __future__ import annotations

import json
import logging
import os
import re
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

# Applied when models.yml omits model_policy (explicit, config-shaped — not a model id).
_DEFAULT_MODEL_POLICY: dict[str, Any] = {
    "boot": {"select": "first"},
    "unavailable": {"select": "first", "skip_requested": True},
}


class ModelCatalogueError(RuntimeError):
    """Raised when the live LiteLLM catalogue cannot satisfy a model policy."""


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


def load_model_policy() -> dict[str, Any]:
    """Return merged model_policy from models.yml (boot + unavailable sections)."""
    provider = _load_litellm_provider()
    if not isinstance(provider, dict):
        return dict(_DEFAULT_MODEL_POLICY)
    raw = provider.get("model_policy")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_MODEL_POLICY)
    boot = raw.get("boot")
    unavailable = raw.get("unavailable")
    policy: dict[str, Any] = {
        "boot": (
            dict(boot)
            if isinstance(boot, dict)
            else dict(_DEFAULT_MODEL_POLICY["boot"])
        ),
        "unavailable": (
            dict(unavailable)
            if isinstance(unavailable, dict)
            else dict(_DEFAULT_MODEL_POLICY["unavailable"])
        ),
    }
    return policy


def _filter_ids(ids: list[str], policy_section: dict[str, Any]) -> list[str]:
    filt = policy_section.get("filter")
    if not isinstance(filt, dict):
        return list(ids)

    include_prefix = filt.get("include_prefix")
    if isinstance(include_prefix, list):
        prefixes = [str(p) for p in include_prefix if str(p)]
        if prefixes:
            ids = [
                model_id
                for model_id in ids
                if any(model_id.startswith(p) for p in prefixes)
            ]

    include_pattern = filt.get("include_pattern")
    if isinstance(include_pattern, str) and include_pattern.strip():
        pattern = re.compile(include_pattern)
        ids = [model_id for model_id in ids if pattern.search(model_id)]

    exclude_contains = filt.get("exclude_contains")
    if isinstance(exclude_contains, list):
        needles = [str(n).lower() for n in exclude_contains if str(n)]
        if needles:
            ids = [
                model_id
                for model_id in ids
                if not any(needle in model_id.lower() for needle in needles)
            ]

    return ids


def _select_from_catalogue(
    ids: list[str],
    policy_section: dict[str, Any],
    *,
    skip: str | None = None,
) -> str | None:
    candidates = _filter_ids(ids, policy_section)
    if skip:
        candidates = [model_id for model_id in candidates if model_id != skip]
    if not candidates:
        return None

    select = str(policy_section.get("select", "first")).strip().lower()
    if select == "last":
        return candidates[-1]
    if select == "max_lex":
        return max(candidates)
    return candidates[0]


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


def resolve_boot_model() -> str:
    """Pick a catalogue id so RpcClient.start() succeeds — not the agent default.

    The per-turn model comes from hub ``model_cfg`` via ``RpcBridge.run(model=…)``.
    This boot pick only needs any id present in ``GET /models``.
    """
    policy = load_model_policy()
    ids = fetch_catalogue_model_ids()
    picked = _select_from_catalogue(ids, policy["boot"])
    if picked:
        log.info(
            "omp model catalogue: boot model %s (policy boot.select=%s)",
            picked,
            policy["boot"].get("select"),
        )
        return picked
    raise ModelCatalogueError(
        "LiteLLM catalogue empty or no model matches model_policy.boot "
        f"(baseUrl models fetch returned {len(ids)} id(s))"
    )


def resolve_fallback_model(*, requested: str | None = None) -> str | None:
    """Resolve a fallback id when the requested model is unavailable (mid-turn)."""
    policy = load_model_policy()["unavailable"]
    skip = requested if policy.get("skip_requested", True) else None
    ids = fetch_catalogue_model_ids()
    return _select_from_catalogue(ids, policy, skip=skip)


# Back-compat aliases used by RpcBridge / tests.
def resolve_startup_model() -> str:
    return resolve_boot_model()


def first_registry_model() -> str | None:
    return resolve_fallback_model(requested=None)