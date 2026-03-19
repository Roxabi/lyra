"""Bootstrap config helpers — raw config loading and parsing."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.messages import MessageManager
from lyra.core.pairing import PairingConfig

log = logging.getLogger(__name__)

_CB_DEFAULTS: dict[str, int] = {"failure_threshold": 5, "recovery_timeout": 60}
_CB_SERVICES = ("anthropic", "telegram", "discord", "hub")
_ADMIN_ID_PATTERN = re.compile(r"^(tg|dc):user:\d+$")


def _load_raw_config(config_path: str | None = None) -> dict:
    """Open and parse config.toml once; return the raw dict.

    Resolution order: $LYRA_CONFIG env var → 'config.toml' in cwd → empty dict.
    """
    raw: dict = {}
    path = config_path or os.environ.get("LYRA_CONFIG", "config.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        log.info("Loaded config from %s", path)
    except FileNotFoundError:
        log.info("No config file at %s — using defaults", path)
    return raw


def _build_agent_overrides(raw: dict, name: str) -> dict:
    """Build instance-level overrides for an agent from config.toml.

    Merges [defaults] with [agents.<name>], with agent-specific values winning.
    Provides machine-specific fallbacks for cwd, persona, and workspaces that
    are not versioned in agents/*.toml.
    """
    defaults: dict = raw.get("defaults", {})
    agent_specific: dict = raw.get("agents", {}).get(name, {})
    merged = {**defaults, **agent_specific}
    # workspaces need a deep merge: defaults provide base, agent-specific keys win
    ws_defaults = defaults.get("workspaces", {})
    ws_agent = agent_specific.get("workspaces", {})
    if ws_defaults or ws_agent:
        merged["workspaces"] = {**ws_defaults, **ws_agent}
    return merged


def _load_circuit_config(
    raw: dict,
) -> tuple[CircuitRegistry, set[str]]:
    """Load [circuit_breaker.*] and [admin] sections from raw config dict.

    Returns (CircuitRegistry with 4 named CBs, set of admin user_ids).
    Missing sections → all defaults.
    """
    cb_section = raw.get("circuit_breaker", {})
    registry = CircuitRegistry()
    for name in _CB_SERVICES:
        cfg: dict[str, int] = {**_CB_DEFAULTS, **cb_section.get(name, {})}
        registry.register(
            CircuitBreaker(
                name=name,
                failure_threshold=cfg["failure_threshold"],
                recovery_timeout=cfg["recovery_timeout"],
            )
        )

    admin_ids: set[str] = set(raw.get("admin", {}).get("user_ids", []))
    for aid in admin_ids:
        if not _ADMIN_ID_PATTERN.match(aid):
            log.warning(
                "Admin ID %r does not match expected format "
                "(tg|dc):user:<digits> — verify config.toml [admin].user_ids",
                aid,
            )
    return registry, admin_ids


def _load_pairing_config(raw: dict) -> PairingConfig:
    """Load [pairing] section from raw config dict. Missing section → all defaults."""
    pairing_section: dict = raw.get("pairing", {})
    return PairingConfig.from_dict(pairing_section)


def _load_cli_pool_config(raw: dict) -> dict:
    """Load [cli_pool] section from raw config dict. Missing keys → defaults."""
    section: dict = raw.get("cli_pool", {})
    return {
        "idle_ttl": section.get("idle_ttl", 1200),
        "default_timeout": section.get("default_timeout", 1200),  # 20 min × 3 = 60 min
        "turn_timeout": section.get("turn_timeout"),
    }


def _load_messages(language: str = "en") -> MessageManager:
    """Load MessageManager.

    Resolution order:
      LYRA_MESSAGES_CONFIG env var → messages.toml in cwd → bundled config.
    """
    bundled = Path(__file__).resolve().parent.parent / "config" / "messages.toml"
    path_str = os.environ.get("LYRA_MESSAGES_CONFIG") or (
        "messages.toml" if Path("messages.toml").exists() else str(bundled)
    )
    if not path_str.endswith(".toml"):
        log.warning(
            "LYRA_MESSAGES_CONFIG %r does not end with .toml — ignoring, using bundled",
            path_str,
        )
        path_str = str(bundled)
    log.info("Loaded messages from %s", path_str)
    return MessageManager(path_str, language=language)
