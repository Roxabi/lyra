"""Compatibility shim — JsonAgentStore moved to lyra.core.stores (S1)."""
from .stores.json_agent_store import *  # noqa: F401, F403
from .stores.json_agent_store import JsonAgentStore

__all__ = ["JsonAgentStore"]
