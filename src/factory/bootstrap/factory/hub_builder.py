"""Hub construction helpers for standalone Hub bootstrap.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

# Re-exports from new domain-specific modules (#1587)
from factory.bootstrap.factory.hub.hub_agent_registration import register_agents
from factory.bootstrap.factory.hub.hub_assembly import _build_hub_and_wire
from factory.bootstrap.factory.hub.hub_cli_pool import build_cli_pool
from factory.bootstrap.factory.hub.hub_clipool_init import _init_clipool
from factory.bootstrap.factory.hub.hub_core import _build_hub
from factory.bootstrap.factory.hub.hub_inbound_bus import build_inbound_bus
from factory.bootstrap.factory.hub.hub_llm_client import build_llm_client

__all__ = [
    "_build_hub",
    "_build_hub_and_wire",
    "_init_clipool",
    "build_cli_pool",
    "build_inbound_bus",
    "build_llm_client",
    "register_agents",
]
