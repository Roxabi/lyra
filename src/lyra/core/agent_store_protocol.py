"""Compatibility shim — AgentStoreProtocol moved to lyra.core.stores (S1)."""
from .stores.agent_store_protocol import *  # noqa: F401, F403
from .stores.agent_store_protocol import AgentStoreProtocol, make_agent_store

__all__ = ["AgentStoreProtocol", "make_agent_store"]
