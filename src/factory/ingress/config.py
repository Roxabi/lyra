"""Ingress connector configuration (ingress.toml + env overrides)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

_ENV_SECRET_PATHS: dict[str, str] = {
    "github": "INGRESS_GITHUB_WEBHOOK_SECRET_PATH",
    "cloudflare": "INGRESS_CLOUDFLARE_WEBHOOK_SECRET_PATH",
}


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    enabled: bool
    webhook_secret: str


@dataclass(frozen=True, slots=True)
class IngressConfig:
    connectors: dict[str, ConnectorConfig]

    def is_enabled(self, name: str) -> bool:
        entry = self.connectors.get(name)
        return entry is not None and entry.enabled


def _read_secret_file(path: str) -> str:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"empty webhook secret at {path}")
    return raw


def _secret_from_env(env_var: str) -> str | None:
    path = os.environ.get(env_var, "").strip()
    if not path:
        return None
    try:
        return _read_secret_file(path)
    except ValueError:
        return None


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_config(*, config_path: Path | None = None) -> IngressConfig:
    """Load ingress connector registry from TOML + env secret paths."""
    path = config_path or Path(
        os.environ.get("FACTORY_INGRESS_CONFIG", "").strip()
        or (Path.home() / ".roxabi/factory/ingress.toml")
    )
    data = _load_toml(path)
    connectors_toml = data.get("connector", {})
    connectors: dict[str, ConnectorConfig] = {}

    for name, toml_entry in connectors_toml.items():
        if not isinstance(toml_entry, dict):
            continue
        default_env = f"INGRESS_{name.upper()}_WEBHOOK_SECRET_PATH"
        env_var = _ENV_SECRET_PATHS.get(name, default_env)
        secret = _secret_from_env(env_var)
        if secret is None:
            secret_path = str(toml_entry.get("secret_path", "")).strip()
            secret = _read_secret_file(secret_path) if secret_path else None
        enabled_flag = bool(toml_entry.get("enabled", True))
        enabled = enabled_flag and secret is not None
        connectors[name] = ConnectorConfig(
            enabled=enabled,
            webhook_secret=secret or "",
        )

    return IngressConfig(connectors=connectors)