"""Ingress connector configuration (ingress.toml + env overrides)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    enabled: bool
    webhook_secret: str


@dataclass(frozen=True, slots=True)
class IngressConfig:
    github: ConnectorConfig
    cloudflare: ConnectorConfig


def _read_secret_file(path: str) -> str:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"empty webhook secret at {path}")
    return raw


def _secret_from_env(env_var: str) -> str | None:
    path = os.environ.get(env_var, "").strip()
    if not path:
        return None
    return _read_secret_file(path)


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_config(*, config_path: Path | None = None) -> IngressConfig:
    """Load ingress connector registry.

    Precedence: env secret paths override ingress.toml ``secret_path`` entries.
    A connector is enabled only when enabled=true AND a non-empty secret is available.
    """
    path = config_path or Path(
        os.environ.get("FACTORY_INGRESS_CONFIG", "").strip()
        or (Path.home() / ".roxabi/factory/ingress.toml")
    )
    data = _load_toml(path)
    connectors = data.get("connector", {})

    gh_toml = connectors.get("github", {})
    cf_toml = connectors.get("cloudflare", {})

    gh_secret = _secret_from_env("INGRESS_GITHUB_WEBHOOK_SECRET_PATH")
    if gh_secret is None:
        gh_path = str(gh_toml.get("secret_path", "")).strip()
        gh_secret = _read_secret_file(gh_path) if gh_path else None

    cf_secret = _secret_from_env("INGRESS_CLOUDFLARE_WEBHOOK_SECRET_PATH")
    if cf_secret is None:
        cf_path = str(cf_toml.get("secret_path", "")).strip()
        cf_secret = _read_secret_file(cf_path) if cf_path else None

    gh_enabled = bool(gh_toml.get("enabled", True)) and gh_secret is not None
    cf_enabled = bool(cf_toml.get("enabled", True)) and cf_secret is not None

    return IngressConfig(
        github=ConnectorConfig(enabled=gh_enabled, webhook_secret=gh_secret or ""),
        cloudflare=ConnectorConfig(enabled=cf_enabled, webhook_secret=cf_secret or ""),
    )