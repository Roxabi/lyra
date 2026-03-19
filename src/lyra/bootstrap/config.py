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
    """Load [cli_pool] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    idle_ttl              : int   — idle process eviction TTL in seconds (default: 1200)
    default_timeout       : int   — per-readline idle timeout in seconds (default: 1200)
    turn_timeout          : float | None — pool turn timeout (default: None)
    reaper_interval       : int   — idle reaper sleep interval in seconds (default: 60)
    kill_timeout          : float — process exit wait timeout (default: 5)
    read_buffer_bytes     : int   — asyncio subprocess stdout buffer (default: 1 MB)
    stdin_drain_timeout   : float — stdin drain timeout in seconds (default: 10.0)
    max_idle_retries      : int   — max readline retries on timeout (default: 3)
    intermediate_timeout  : float — on_intermediate callback timeout (default: 5.0)
    """
    section: dict = raw.get("cli_pool", {})
    return {
        "idle_ttl": section.get("idle_ttl", 1200),
        "default_timeout": section.get("default_timeout", 1200),  # 20 min × 3 = 60 min
        "turn_timeout": section.get("turn_timeout"),
        "reaper_interval": int(section.get("reaper_interval", 60)),
        "kill_timeout": float(section.get("kill_timeout", 5.0)),
        "read_buffer_bytes": int(section.get("read_buffer_bytes", 1024 * 1024)),
        "stdin_drain_timeout": float(section.get("stdin_drain_timeout", 10.0)),
        "max_idle_retries": int(section.get("max_idle_retries", 3)),
        "intermediate_timeout": float(section.get("intermediate_timeout", 5.0)),
    }


def _load_hub_config(raw: dict) -> dict:
    """Load [hub] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    pool_ttl     : float  — idle pool eviction TTL in seconds (default: 604800 / 7 days)
    bus_size     : int    — inbound bus queue capacity (default: 100)
    rate_limit   : int    — max messages per rate window per user (default: 20)
    rate_window  : int    — rate window duration in seconds (default: 60)
    """
    section: dict = raw.get("hub", {})
    return {
        "pool_ttl": float(section.get("pool_ttl", 604800.0)),
        "bus_size": int(section.get("bus_size", 100)),
        "rate_limit": int(section.get("rate_limit", 20)),
        "rate_window": int(section.get("rate_window", 60)),
    }


def _load_pool_config(raw: dict) -> dict:
    """Load [pool] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    max_sdk_history      : int   — max SDK history entries per pool (default: 50)
    safe_dispatch_timeout: float — dispatch_response timeout (default: 10.0)
    """
    section: dict = raw.get("pool", {})
    return {
        "max_sdk_history": int(section.get("max_sdk_history", 50)),
        "safe_dispatch_timeout": float(section.get("safe_dispatch_timeout", 10.0)),
    }


def _load_llm_config(raw: dict) -> dict:
    """Load [llm] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    max_retries  : int   — RetryDecorator max retries on transient failures (default: 3)
    backoff_base : float — RetryDecorator exponential backoff base (default: 1.0)
    max_prompt_bytes : int — max system_prompt size in bytes (default: 65536 / 64 KB)
    """
    section: dict = raw.get("llm", {})
    return {
        "max_retries": int(section.get("max_retries", 3)),
        "backoff_base": float(section.get("backoff_base", 1.0)),
        "max_prompt_bytes": int(section.get("max_prompt_bytes", 64 * 1024)),
    }


def _load_inbound_bus_config(raw: dict) -> dict:
    """Load [inbound_bus] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    queue_depth_threshold  : int — staging depth warning threshold (default: 100)
    staging_maxsize        : int — staging queue capacity (default: 500)
    platform_queue_maxsize : int — per-platform queue capacity (default: 100)
    """
    section: dict = raw.get("inbound_bus", {})
    return {
        "queue_depth_threshold": int(section.get("queue_depth_threshold", 100)),
        "staging_maxsize": int(section.get("staging_maxsize", 500)),
        "platform_queue_maxsize": int(section.get("platform_queue_maxsize", 100)),
    }


def _load_debouncer_config(raw: dict) -> dict:
    """Load [debouncer] section from raw config dict. Missing keys → defaults.

    Keys
    ----
    default_debounce_ms : int — rapid-message aggregation window in ms (default: 300)
    max_merged_chars    : int — max combined text length after merge (default: 4096)
    """
    section: dict = raw.get("debouncer", {})
    return {
        "default_debounce_ms": int(section.get("default_debounce_ms", 300)),
        "max_merged_chars": int(section.get("max_merged_chars", 4096)),
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
