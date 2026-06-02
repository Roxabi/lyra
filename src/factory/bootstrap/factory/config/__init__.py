"""Bootstrap config helpers — raw config loading and parsing.

Re-export barrel for backward compatibility. Domain-specific helpers live in
config_deprecation.py, config_models.py, and config_loader.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from factory.bootstrap.factory.config.config_deprecation import (
    warn_deprecated_bot_sections,
)
from factory.bootstrap.factory.config.config_loader import (
    _build_agent_overrides,
    _load_circuit_config,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_logging_config,
    _load_messages,
    _load_pairing_config,
    _load_pool_config,
    _load_raw_config,
    _load_tool_display_config,
    _validate_config_path,
)
from factory.bootstrap.factory.config.config_models import (
    AgentOverrideConfig,
    CliPoolConfig,
    DebouncerConfig,
    EventBusConfig,
    HubConfig,
    InboundBusConfig,
    LlmConfig,
    LoggingConfig,
    MessageIndexConfig,
    PoolConfig,
)
from factory.core.messaging.tool_display_config import ToolDisplayConfig

__all__ = [
    "warn_deprecated_bot_sections",
    "_build_agent_overrides",
    "_load_circuit_config",
    "_load_cli_pool_config",
    "_load_debouncer_config",
    "_load_event_bus_config",
    "_load_hub_config",
    "_load_inbound_bus_config",
    "_load_llm_config",
    "_load_logging_config",
    "_load_messages",
    "_load_pairing_config",
    "_load_pool_config",
    "_load_raw_config",
    "_load_tool_display_config",
    "_validate_config_path",
    "AgentOverrideConfig",
    "CliPoolConfig",
    "DebouncerConfig",
    "EventBusConfig",
    "HubConfig",
    "InboundBusConfig",
    "LlmConfig",
    "LoggingConfig",
    "MessageIndexConfig",
    "PoolConfig",
    "AdapterConfigBundle",
    "build_adapter_config_bundle",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdapterConfigBundle:
    """Single composition root for adapter-scoped config.

    Extracted per ADR-073 to prevent N×M duplication when new bootstrap
    paths (wired, standalone, embedded) are added.
    """

    tool_display: ToolDisplayConfig


def build_adapter_config_bundle(raw_config: dict[str, Any]) -> AdapterConfigBundle:
    """Build AdapterConfigBundle from raw config dict.

    All bootstrap paths (wired, standalone, embedded) call this single
    factory instead of duplicating _load_tool_display_config calls.
    """
    return AdapterConfigBundle(
        tool_display=_load_tool_display_config(raw_config),
    )
