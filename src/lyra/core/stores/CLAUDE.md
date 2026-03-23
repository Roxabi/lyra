# src/lyra/core/stores/ — SQLite Persistence Layer

## Purpose

All durable stores, the shared `SqliteStore` base class, and pairing state.
Every store that writes to SQLite lives here.

## Files

| File | Responsibility |
|------|---------------|
| `sqlite_base.py` | `SqliteStore` ABC — shared base for all stores (migrations, connect, close) |
| `agent_store.py` | SQLite-backed agent config store (write-through cache); `AgentRow`, `AgentStore` |
| `agent_store_protocol.py` | `AgentStoreProtocol` structural protocol + `make_agent_store()` factory |
| `json_agent_store.py` | In-memory `JsonAgentStore` — DB-free stub for testing |
| `auth_store.py` | `AuthStore` — user trust levels and auth state |
| `credential_store.py` | `CredentialStore` — encrypted credential storage |
| `message_index.py` | `MessageIndex` — message deduplication and lookup |
| `pairing.py` | `PairingStore` — bot-to-agent pairing persistence |
| `pairing_config.py` | `PairingConfig` dataclass — pairing data shape (no DB logic) |
| `prefs_store.py` | `PrefsStore` — per-user preference storage |
| `thread_store.py` | `ThreadStore` — conversation thread tracking |
| `turn_store.py` | `TurnStore` — turn history persistence |

## Import pattern

```python
# Subpackage re-exports — only the 4 most-used abstract types + factory:
#   AgentStore, AgentStoreProtocol, AuthStore, SqliteStore, make_agent_store
from lyra.core.stores import AgentStore, AuthStore, SqliteStore, AgentStoreProtocol

# Direct module imports — required for all other types:
from lyra.core.stores.agent_store import AgentRow, AgentStore, AgentRuntimeStateRow
from lyra.core.stores.agent_store_protocol import AgentStoreProtocol, make_agent_store
from lyra.core.stores.pairing import PairingStore
from lyra.core.stores.thread_store import ThreadStore
from lyra.core.stores.credential_store import CredentialStore
from lyra.core.stores.turn_store import TurnStore
from lyra.core.stores.prefs_store import PrefsStore
from lyra.core.stores.message_index import MessageIndex
from lyra.core.stores.pairing_config import PairingConfig
```

## Gotchas

- `sqlite_base.py` is the shared base — all stores inherit `SqliteStore`. Always
  subclass it; never instantiate `SqliteStore` directly.
- All stores follow the async store pattern: `__init__` does no I/O; `connect()`
  opens the DB and runs migrations; `close()` tears down. Never call async methods
  before `connect()`.
- Reads are synchronous (from in-memory cache). Writes are async (SQLite).
  The cache is updated atomically with the write — the event loop never blocks on a read.
- `pairing_config.py` is a sibling dataclass with no DB logic. It is imported by
  `pairing.py` as the data shape for pairing rows.
- `json_agent_store.py` implements `AgentStoreProtocol` without SQLite — use it in
  tests that must not touch the filesystem. Instantiate via `make_agent_store(use_json=True)`.
- Import directly from `lyra.core.stores` — no compat shims exist at flat paths.
