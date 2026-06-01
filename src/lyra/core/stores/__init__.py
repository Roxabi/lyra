"""Stores package — protocols and factory functions only.

SQLite implementations have been moved to lyra.infrastructure.stores per
ADR-048 (absorbed into ADR-059).
This package re-exports only protocol-safe symbols for backward compatibility.
"""

from .agent_store_protocol import AgentStoreProtocol
from .bot_store_protocol import BotStoreProtocol
from .message_index_protocol import MessageIndexProtocol
from .thread_store_protocol import ThreadStoreProtocol
from .turn_store_protocol import SessionRow, TurnRow, TurnStoreProtocol

__all__ = [
    "AgentStoreProtocol",
    "BotStoreProtocol",
    "MessageIndexProtocol",
    "SessionRow",
    "ThreadStoreProtocol",
    "TurnRow",
    "TurnStoreProtocol",
]
