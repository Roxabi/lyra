"""SQLite store implementations — moved from factory.core.stores per
ADR-048 (absorbed into ADR-059)."""

from factory.infrastructure.stores.agent_store import AgentStore
from factory.infrastructure.stores.auth_store import AuthStore
from factory.infrastructure.stores.base.bot_agent_map import BotAgentMapStore
from factory.infrastructure.stores.base.message_index import MessageIndex
from factory.infrastructure.stores.base.sqlite_base import SqliteStore
from factory.infrastructure.stores.bot_store import BotStore
from factory.infrastructure.stores.identity_alias_store import IdentityAliasStore
from factory.infrastructure.stores.message_index_kv import MessageIndexKvStore
from factory.infrastructure.stores.pairing import (
    PairingManager,
    get_pairing_manager,
    set_pairing_manager,
)
from factory.infrastructure.stores.prefs_store import PrefsStore, UserPrefs
from factory.infrastructure.stores.thread_store import ThreadStore
from factory.infrastructure.stores.turn_store import TurnStore

__all__ = [
    "AgentStore",
    "AuthStore",
    "BotAgentMapStore",
    "BotStore",
    "IdentityAliasStore",
    "MessageIndex",
    "MessageIndexKvStore",
    "PairingManager",
    "PrefsStore",
    "SqliteStore",
    "ThreadStore",
    "TurnStore",
    "UserPrefs",
    "get_pairing_manager",
    "set_pairing_manager",
]
