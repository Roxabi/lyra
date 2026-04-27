"""Stores package — protocols and factory functions only.

SQLite implementations have been moved to lyra.infrastructure.stores per ADR-048.
This package re-exports only protocol-safe symbols for backward compatibility.
"""

from .agent_store_protocol import AgentStoreProtocol
from .thread_store_protocol import ThreadStoreProtocol

__all__ = [
    "AgentStoreProtocol",
    "ThreadStoreProtocol",
]
