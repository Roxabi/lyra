"""Bootstrap config helpers — raw config loading and parsing."""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.messages import MessageManager
from lyra.core.stores.pairing_config import PairingConfig
from lyra.core.tool_display_config import ToolDisplayConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic config section models (#411)
# ---------------------------------------------------------------------------


class CliPoolConfig(BaseModel):
    """Typed [cli_pool] config section."""

    model_config = ConfigDict(frozen=True)

    idle_ttl: int = 1200
    default_timeout: int = 1200
    turn_timeout: float | None = None
    reaper_interval: int = 60
    kill_timeout: float = 5.0
    read_buffer_bytes: int = 1024 * 1024
    stdin_drain_timeout: float = 10.0
    max_idle_retries: int = 3
    intermediate_timeout: float = 5.0


class HubConfig(BaseModel):
    """Typed [hub] config section."""

    model_config = ConfigDict(frozen=True)

    pool_ttl: float = 604800.0
    rate_limit: int = 20
    rate_window: int = 60


class PoolConfig(BaseModel):
    """Typed [pool] config section."""

    model_config = ConfigDict(frozen=True)

    max_sdk_history: int = 50
    safe_dispatch_timeout: float = 10.0


class LlmConfig(BaseModel):
    """Typed [llm] config section."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    backoff_base: float = 1.0


class InboundBusConfig(BaseModel):
    """Typed [inbound_bus] config section."""

    model_config = ConfigDict(frozen=True)

    queue_depth_threshold: int = 100
    staging_maxsize: int = 500
    platform_queue_maxsize: int = 100


class DebouncerConfig(BaseModel):
    """Typed [debouncer] config section."""

    model_config = ConfigDict(frozen=True)

    default_debounce_ms: int = 300
    max_merged_chars: int = 4096
    cancel_on_new_message: bool = False


class AgentOverrideConfig(BaseModel):
    """Typed output of _build_agent_overrides().

    Uses extra="ignore" — only cwd, persona, workspaces are consumed; extra keys
    are silently discarded.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    cwd: str | None = None
    persona: str | None = None
    workspaces: dict[str, str] = {}

_CB_DEFAULTS: dict[str, int] = {"failure_threshold": 5, "recovery_timeout": 60}
_CB_SERVICES = ("anthropic", "telegram", "discord", "hub")
_ADMIN_ID_PATTERN = re.compile(r"^(tg|dc):user:\d+$")


def _load_raw_config(config_path: str | None = None) -> dict[str, Any]:
    """Open and parse config.toml once; return the raw dict.

    Resolution order: $LYRA_CONFIG env var → 'config.toml' in cwd → empty dict.
    """
    raw: dict[str, Any] = {}
    path = config_path or os.environ.get("LYRA_CONFIG", "config.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        log.info("Loaded config from %s", path)
    except FileNotFoundError:
        log.info("No config file at %s — using defaults", path)
    return raw


def _build_agent_overrides(raw: dict[str, Any], name: str) -> AgentOverrideConfig:
    """Build instance-level overrides for an agent from config.toml.

    Merges [defaults] with [agents.<name>], with agent-specific values winning.
    Provides machine-specific fallbacks for cwd, persona, and workspaces that
    are not versioned in agents/*.toml.
    """
    defaults: dict[str, Any] = raw.get("defaults", {})
    agent_specific: dict[str, Any] = raw.get("agents", {}).get(name, {})
    merged = {**defaults, **agent_specific}
    # workspaces need a deep merge: defaults provide base, agent-specific keys win
    ws_defaults: dict[str, str] = defaults.get("workspaces", {})
    ws_agent: dict[str, str] = agent_specific.get("workspaces", {})
    if ws_defaults or ws_agent:
        merged["workspaces"] = {**ws_defaults, **ws_agent}
    return AgentOverrideConfig.model_validate(merged)


def _load_circuit_config(
    raw: dict[str, Any],
) -> tuple[CircuitRegistry, frozenset[str]]:
    """Load [circuit_breaker.*] and [admin] sections from raw config dict.

    Returns (CircuitRegistry with 4 named CBs, frozenset of admin user_ids).
    Missing sections → all defaults.
    """
    cb_section: dict[str, Any] = raw.get("circuit_breaker", {})
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

    admin_ids: frozenset[str] = frozenset(raw.get("admin", {}).get("user_ids", []))
    for aid in admin_ids:
        if not _ADMIN_ID_PATTERN.match(aid):
            log.warning(
                "Admin ID %r does not match expected format "
                "(tg|dc):user:<digits> — verify config.toml [admin].user_ids",
                aid,
            )
    return registry, admin_ids


def _load_pairing_config(raw: dict[str, Any]) -> PairingConfig:
    """Load [pairing] section from raw config dict. Missing section → all defaults."""
    pairing_section: dict[str, Any] = raw.get("pairing", {})
    return PairingConfig.model_validate(pairing_section)


def _load_tool_display_config(raw: dict[str, Any]) -> ToolDisplayConfig:
    """Load [tool_display] section from raw config dict. Missing section → all defaults.

    Keys (all optional)
    -------------------
    names_threshold : int  — edits before switching to count mode per file (default: 3)
    group_threshold : int  — files before switching to grouped display (default: 3)
    bash_max_len    : int  — max chars per bash command shown (default: 60)
    throttle_ms     : int  — min ms between ToolSummaryRenderEvent emits (default: 2000)

    [tool_display.show]   — per-tool visibility overrides (merged with defaults)
    """
    section: dict[str, Any] = raw.get("tool_display", {})
    return ToolDisplayConfig.model_validate(section)


def _load_cli_pool_config(raw: dict[str, Any]) -> CliPoolConfig:
    """Load [cli_pool] section from raw config dict. Missing keys → defaults."""
    return CliPoolConfig.model_validate(raw.get("cli_pool", {}))


def _load_hub_config(raw: dict[str, Any]) -> HubConfig:
    """Load [hub] section from raw config dict. Missing keys → defaults."""
    return HubConfig.model_validate(raw.get("hub", {}))


def _load_pool_config(raw: dict[str, Any]) -> PoolConfig:
    """Load [pool] section from raw config dict. Missing keys → defaults."""
    return PoolConfig.model_validate(raw.get("pool", {}))


def _load_llm_config(raw: dict[str, Any]) -> LlmConfig:
    """Load [llm] section from raw config dict. Missing keys → defaults."""
    return LlmConfig.model_validate(raw.get("llm", {}))


def _load_inbound_bus_config(raw: dict[str, Any]) -> InboundBusConfig:
    """Load [inbound_bus] section from raw config dict. Missing keys → defaults."""
    return InboundBusConfig.model_validate(raw.get("inbound_bus", {}))


def _load_debouncer_config(raw: dict[str, Any]) -> DebouncerConfig:
    """Load [debouncer] section from raw config dict. Missing keys → defaults."""
    return DebouncerConfig.model_validate(raw.get("debouncer", {}))


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
