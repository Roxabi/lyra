"""SQLite store implementations (migrated from lyra.core.stores per ADR-048)."""

from .agent_store import AgentRow, AgentRuntimeStateRow, AgentStore, BotAgentMapRow
from .agent_store_migrations import run_agent_migrations
from .auth_store import AuthStore
from .credential_store import BotSecretRow, CredentialStore, LyraKeyring
from .identity_alias_store import IdentityAliasStore
from .message_index import MessageIndex
from .pairing import PairingConfig, PairingError, PairingManager, set_pairing_manager
from .prefs_store import PrefsStore, UserPrefs
from .sqlite_base import SqliteStore
from .thread_store import ThreadStore
from .turn_store import TurnStore
from .turn_store_queries import backfill_sessions

__all__ = [
    # Agent store
    "AgentRow",
    "AgentStore",
    "AgentRuntimeStateRow",
    "BotAgentMapRow",
    "run_agent_migrations",
    # Auth store
    "AuthStore",
    # Credential store
    "BotSecretRow",
    "CredentialStore",
    "LyraKeyring",
    # Identity alias store
    "IdentityAliasStore",
    # Message index
    "MessageIndex",
    # Pairing
    "PairingConfig",
    "PairingError",
    "PairingManager",
    "set_pairing_manager",
    # Prefs store
    "PrefsStore",
    "UserPrefs",
    # SQLite base
    "SqliteStore",
    # Thread store
    "ThreadStore",
    # Turn store
    "TurnStore",
    "backfill_sessions",
]
