---
title: Hexagonal Architecture Remediation — Lyra
description: Spec for resolving all layer-violation violations found in the 2026-04 audit.
---

# Hexagonal Architecture Remediation — Lyra

Audit date: 2026-04-27. Layer rule: `core ← llm/nats ← infrastructure ← adapters ← bootstrap`.
All violations sourced from `src/lyra/`. References are relative to that root.

---

## Summary Table

| ID | File | Violation | Fix | Est. lines changed | Priority |
|----|------|-----------|-----|--------------------|----------|
| V1 | `core/stores/agent_store_protocol.py:106` | `make_agent_store()` runtime-imports `AgentStore` (infra) inside domain | Move factory to `bootstrap/factory/agent_store_factory.py`; keep only Protocol in `core/stores/` | ~30 | P0 |
| V2 | `core/stores/agent_store_migrations.py:7` | `aiosqlite` migration runner in `core/stores/` | Move to `infrastructure/stores/agent_store_migrations.py`; update 1 import | ~5 | P0 |
| V3 | `commands/pairing/handlers.py:13` | Direct import of `get_pairing_manager` + `PairingError` from `lyra.infrastructure.stores.pairing` | Add `PairingManagerProtocol` + `PairingError` to `core/stores/pairing_protocol.py`; inject via DI | ~40 | P0 |
| V4 | `commands/identity/handlers.py:10` | Direct import of concrete `IdentityAliasStore` from infrastructure | Add `IdentityAliasStoreProtocol` to `core/stores/`; change handler type hint | ~15 | P0 |
| V5 | `adapters/discord/adapter.py:51` | `ThreadStore` (concrete SQLite) imported directly | Add `ThreadStoreProtocol` to `core/stores/`; inject via Application layer | ~20 | P1 |
| V6 | `config.py:25-26` | Imports `DiscordConfig`, `TelegramConfig` from `adapters.*` | Move pure Pydantic config dataclasses to `core/config/` (or `infrastructure/config/`); adapters re-export from there | ~60 | P1 |
| V7 | `llm/base.py` | `LlmProvider` Protocol lives in `llm/` (Infrastructure tier) | Move to `core/ports/llm.py`; `llm/base.py` re-exports for backward compat | ~10 | P1 |
| V8 | `stt/__init__.py`, `tts/__init__.py` | `STTProtocol`, `TtsProtocol` outside `core/` | Move to `core/ports/stt.py` and `core/ports/tts.py`; original files become thin re-exports | ~20 | P1 |
| V9 | `.importlinter` | `lyra.commands` and `lyra.agents` absent from forbidden contracts | Add two forbidden contracts blocking direct infra imports | ~20 | P1 |
| V10 | `bootstrap/factory/unified.py` | 320 lines, 8+ responsibilities | Extract `_wire_stores()`, `_wire_llm()`, `_wire_adapters()` into `bootstrap/factory/wiring_helpers.py` | ~80 | P2 |
| V11 | `core/pool/pool.py` | 312 lines; contains backward-compat shims for `PoolObserver` | Remove shims; keep only live code | ~30 | P2 |
| V12 | `agents/simple_agent.py:121-143` | `_register_session_commands()` assembles `SessionTools` internally | Receive pre-built `SessionTools` via DI from bootstrap | ~35 | P2 |

---

## Dependency Order

Fixes must land in this order to avoid broken intermediate states:

```
V2 → V1    (migration runner must be in infra before factory moves there)
V8 → V5    (ThreadStoreProtocol needs core/ports/ to exist; V8 establishes core/ports/)
V7 → V8    (create core/ports/ with LlmProvider first; STT/TTS follow)
V3, V4     (independent; can run in parallel after V2)
V6         (independent; no upstream deps)
V9         (must be last — validates that V1–V8 don't re-introduce violations)
V10, V11, V12  (P2; independent of each other; after P0/P1 complete)
```

Recommended batch order:
1. V2 → V7 → V8 (establish `core/ports/`, move migrations)
2. V1, V3, V4, V5 (parallel; remove P0 infra imports from domain)
3. V6 (config relocation)
4. V9 (importlinter contracts)
5. V10, V11, V12 (P2 cleanup)

---

## Per-Violation Sections

### V1 — `make_agent_store()` factory in Domain

**Current (`core/stores/agent_store_protocol.py:96-112`):**
```python
def make_agent_store(...):
    if os.environ.get("LYRA_DB") == "json":
        from .json_agent_store import JsonAgentStore
        ...
        return JsonAgentStore(path=path)

    from lyra.infrastructure.stores.agent_store import AgentStore  # ← violation
    ...
    return AgentStore(db_path=resolved)
```

**Target state:**
- `core/stores/agent_store_protocol.py` — contains only `AgentStoreProtocol` (Protocol class) and `AgentStoreRow`. No factory. No runtime infra imports.
- `bootstrap/factory/agent_store_factory.py` — new file; contains `make_agent_store()` with full infra imports.
- All existing callers of `make_agent_store` updated to import from `bootstrap/factory/agent_store_factory`.

**Acceptance criteria:**
- `grep -r "make_agent_store" src/lyra/core/` returns empty.
- `import-linter` passes with the `core-stores-no-sqlite` contract and the `ignore_imports` transitional exemption removed.
- `lyra agent init` and all tests using `make_agent_store` pass.

---

### V2 — `agent_store_migrations.py` in Domain

**Current (`core/stores/agent_store_migrations.py:7`):**
```python
import aiosqlite  # ← forbidden in core/stores per existing contract
```

**Target state:**
- File moved verbatim to `infrastructure/stores/agent_store_migrations.py`.
- `infrastructure/stores/agent_store.py` — update 1 import line:
  ```python
  # before
  from lyra.core.stores.agent_store_migrations import run_agent_migrations
  # after
  from lyra.infrastructure.stores.agent_store_migrations import run_agent_migrations
  ```
- `core/stores/` no longer contains any `aiosqlite` reference.

**Acceptance criteria:**
- `grep -r "aiosqlite" src/lyra/core/` returns empty.
- `[importlinter:contract:core-stores-no-sqlite]` passes with transitional `ignore_imports` line removed.

---

### V3 — `commands/pairing/handlers.py` imports infra directly

**Current (`commands/pairing/handlers.py:13`):**
```python
from lyra.infrastructure.stores.pairing import PairingError, get_pairing_manager
```

**Target state:**
- New file `core/stores/pairing_protocol.py`:
  ```python
  from typing import Protocol
  class PairingManagerProtocol(Protocol):
      config: PairingConfig
      async def create_invite(self, admin_id: str) -> str: ...
      async def accept_invite(self, code: str, user_id: str) -> None: ...
      async def unpair(self, user_id: str) -> None: ...

  class PairingError(Exception): ...  # base; concrete subclasses remain in infra
  ```
- `infrastructure/stores/pairing.py` — `get_pairing_manager()` returns `PairingManagerProtocol`; `PairingError` re-exported from `core/stores/pairing_protocol`.
- `commands/pairing/handlers.py` — imports from `lyra.core.stores.pairing_protocol`; receives `PairingManagerProtocol` via DI (inject into handler signature or module-level setter, not via global `get_pairing_manager()`).

**Acceptance criteria:**
- `grep "lyra.infrastructure" src/lyra/commands/pairing/handlers.py` returns empty.
- `/invite`, `/join`, `/unpair` commands functional in integration smoke test.

---

### V4 — `commands/identity/handlers.py` imports concrete store

**Current (`commands/identity/handlers.py:10`):**
```python
from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore
```

**Target state:**
- New file `core/stores/identity_alias_protocol.py`:
  ```python
  from typing import Protocol
  class IdentityAliasStoreProtocol(Protocol):
      async def get_canonical(self, platform_id: str) -> str | None: ...
      async def set_alias(self, platform_id: str, canonical_id: str) -> None: ...
      async def remove_alias(self, platform_id: str) -> None: ...
  ```
- `commands/identity/handlers.py` — type hints changed to `IdentityAliasStoreProtocol`; injected, not imported directly.

**Acceptance criteria:**
- `grep "lyra.infrastructure" src/lyra/commands/identity/handlers.py` returns empty.
- `/link` and `/unlink` commands pass unit tests with a mock implementing the protocol.

---

### V5 — `adapters/discord/adapter.py` uses concrete `ThreadStore`

**Current (`adapters/discord/adapter.py:51`):**
```python
from lyra.infrastructure.stores.thread_store import ThreadStore
```
`ThreadStore` (SQLite) passed directly to `DiscordAdapter.__init__`.

**Target state:**
- New file `core/stores/thread_store_protocol.py`:
  ```python
  from typing import Protocol
  class ThreadStoreProtocol(Protocol):
      async def get_owner(self, thread_id: int) -> str | None: ...
      async def set_owner(self, thread_id: int, bot_id: str) -> None: ...
      async def delete(self, thread_id: int) -> None: ...
  ```
- `DiscordAdapter.__init__` signature: `thread_store: ThreadStoreProtocol` (not `ThreadStore`).
- Bootstrap (where `DiscordAdapter` is constructed) passes the concrete `ThreadStore`; adapter only sees the protocol.

**Acceptance criteria:**
- `grep "infrastructure.stores" src/lyra/adapters/discord/adapter.py` returns empty.
- `DiscordAdapter` unit-testable with a mock `ThreadStoreProtocol`.

---

### V6 — `config.py` imports platform config from `adapters.*`

**Current (`config.py:25-26`):**
```python
from lyra.adapters.discord.discord_config import DiscordConfig, load_discord_config
from lyra.adapters.telegram import TelegramConfig, load_config
```

**Target state — Option A (preferred):** Move pure-Pydantic dataclasses only (no aiogram/discord.py imports) to `core/config/platform_configs.py`:
```python
# core/config/platform_configs.py
class TelegramBotConfig(BaseModel): ...
class DiscordBotConfig(BaseModel): ...
class TelegramMultiConfig(BaseModel): ...
class DiscordMultiConfig(BaseModel): ...
```
`adapters/discord/discord_config.py` and `adapters/telegram/__init__.py` re-export from `core/config/platform_configs`. `config.py` imports from `core/config/platform_configs`.

**Acceptance criteria:**
- `grep "lyra.adapters" src/lyra/config.py` returns empty.
- `from lyra.config import TelegramConfig, DiscordConfig` still works for existing callers.

---

### V7 — `LlmProvider` Protocol in `llm/` (wrong tier)

**Current (`llm/base.py:1-8`):**
```python
# lyra/llm/base.py
class LlmProvider(Protocol): ...
class LlmResult: ...
```
`llm/` sits above `core/` in the layer stack — a Protocol defined here cannot be imported by `core/` without violating layers.

**Target state:**
- New file `core/ports/llm.py`:
  ```python
  class LlmProvider(Protocol): ...
  class LlmResult: ...
  ```
- `llm/base.py` becomes a thin re-export shim:
  ```python
  from lyra.core.ports.llm import LlmProvider, LlmResult  # noqa: F401
  ```
- All existing callers remain valid via the shim (no forced migration wave).
- `llm/CLAUDE.md` updated to reflect canonical location.

**Acceptance criteria:**
- `from lyra.core.ports.llm import LlmProvider` succeeds.
- `from lyra.llm.base import LlmProvider` still resolves (shim).
- `core/` can import `LlmProvider` without triggering an import-linter violation.

---

### V8 — `STTProtocol` / `TtsProtocol` outside `core/`

**Current:**
- `stt/__init__.py` — defines `STTProtocol` directly
- `tts/__init__.py` — defines `TtsProtocol` directly

Both packages sit at the same level as `adapters/` — outside `core/`.

**Target state:**
- `core/ports/stt.py` — `STTProtocol`, `STTResult`, related types
- `core/ports/tts.py` — `TtsProtocol`, `TtsConfig`, `SynthesisResult`, related types
- `stt/__init__.py` and `tts/__init__.py` — thin re-exports:
  ```python
  from lyra.core.ports.stt import STTProtocol, STTResult  # noqa: F401
  ```
- Establishes `core/ports/` as the canonical ports package (used by V7).

**Acceptance criteria:**
- `from lyra.core.ports.stt import STTProtocol` succeeds.
- `from lyra.stt import STTProtocol` still resolves (shim).
- `core/` consumers of STTProtocol require no import changes.

---

### V9 — `.importlinter` missing contracts for `lyra.commands` and `lyra.agents`

**Current `.importlinter`:** no contract covers `lyra.commands → lyra.infrastructure` or `lyra.agents → lyra.infrastructure` imports.

**Target state — add two contracts:**

```ini
[importlinter:contract:commands-no-direct-infra]
name = commands must not import infrastructure stores directly
type = forbidden
source_modules =
    lyra.commands
forbidden_modules =
    lyra.infrastructure.stores
allow_indirect_imports = false

[importlinter:contract:agents-no-direct-infra]
name = agents must not import infrastructure stores directly
type = forbidden
source_modules =
    lyra.agents
forbidden_modules =
    lyra.infrastructure.stores
allow_indirect_imports = false
```

Add these stanzas to `.importlinter` after V1–V8 are complete. Running `import-linter` before V3/V4 are fixed will trigger failures — that is the intended gate.

**Acceptance criteria:**
- `lint-imports` passes with zero violations and no `ignore_imports` entries for `lyra.commands` or `lyra.agents`.

---

### V10 — `bootstrap/factory/unified.py` (320 lines, 8+ responsibilities)

**Current:** Single function `_bootstrap_unified()` in `unified.py` performs store opening, LLM wiring, adapter construction, NATS setup, agent registration, health server, and lifecycle.

**Target state:** Extract into `bootstrap/factory/wiring_helpers.py`:
```python
async def _wire_stores(cfg, ...) -> StoreBundle: ...
async def _wire_llm(cfg, stores, ...) -> LlmBundle: ...
async def _wire_adapters(cfg, hub, stores, ...) -> list[ChannelAdapter]: ...
```
`unified.py` becomes an orchestrator calling these helpers — fits within 300 LOC.

**Acceptance criteria:**
- `wc -l src/lyra/bootstrap/factory/unified.py` ≤ 300.
- Each helper ≤ 300 LOC.
- `_bootstrap_unified()` integration test passes.

---

### V11 — `core/pool/pool.py` (312 lines, backward-compat shims)

**Current:** `pool.py` contains shims for the old `PoolObserver` interface (pre-`PoolObserver` stabilisation).

**Target state:** Remove all shim code. `pool.py` ≤ 300 LOC.

**Acceptance criteria:**
- `wc -l src/lyra/core/pool/pool.py` ≤ 300.
- `grep -n "# compat\|# backward\|# shim" src/lyra/core/pool/pool.py` returns empty.
- All pool-dependent tests pass.

---

### V12 — `agents/simple_agent.py` assembles `SessionTools` internally

**Current (`agents/simple_agent.py:121-143`):**
```python
def _register_session_commands(self) -> None:
    from lyra.integrations.vault_cli import VaultCli
    from lyra.integrations.web_intel import WebIntelScraper
    self._session_tools = SessionTools(
        scraper=WebIntelScraper(), vault=VaultCli()
    )
```
Agent constructs infrastructure integrations. This is bootstrap's job.

**Target state:**
- `SimpleAgent.__init__` accepts `session_tools: SessionTools | None = None`.
- `_register_session_commands()` uses `self._session_tools` as received; does not construct `SessionTools`.
- Bootstrap (`factory/agent_factory.py`) constructs `SessionTools` and passes it to `SimpleAgent`.

**Acceptance criteria:**
- `grep "VaultCli\|WebIntelScraper" src/lyra/agents/simple_agent.py` returns empty.
- `SimpleAgent` unit-testable by passing a mock `SessionTools`.

---

## Importlinter Contract Additions (exact stanzas)

Add to `.importlinter` in order, after all code fixes:

```ini
[importlinter:contract:commands-no-direct-infra]
name = commands must not import infrastructure stores directly
type = forbidden
source_modules =
    lyra.commands
forbidden_modules =
    lyra.infrastructure.stores
allow_indirect_imports = false

[importlinter:contract:agents-no-direct-infra]
name = agents must not import infrastructure stores directly
type = forbidden
source_modules =
    lyra.agents
forbidden_modules =
    lyra.infrastructure.stores
allow_indirect_imports = false
```

Also remove these transitional exemptions once V1/V2 are complete:

```ini
# Remove from [importlinter:contract:clean-architecture-layers] ignore_imports:
lyra.core.stores.agent_store_protocol -> lyra.infrastructure.stores.agent_store

# Remove from [importlinter:contract:core-stores-no-sqlite] ignore_imports:
lyra.core.stores.agent_store_migrations -> aiosqlite
```

---

## Definition of Done

### P0 — Blocker (V1–V4)

- [ ] `grep -r "lyra.infrastructure" src/lyra/core/` returns zero results (excluding `TYPE_CHECKING` blocks already exempted in `.importlinter`)
- [ ] `grep -r "lyra.infrastructure" src/lyra/commands/` returns zero results
- [ ] `uv run lint-imports` passes with transitional exemptions for V1/V2 removed
- [ ] `uv run pytest` passes (all units, no regression)

### P1 — Architecture Gate (V5–V9)

- [ ] All Protocols (`LlmProvider`, `STTProtocol`, `TtsProtocol`, `ThreadStoreProtocol`, `IdentityAliasStoreProtocol`, `PairingManagerProtocol`) importable from `lyra.core.ports.*` or `lyra.core.stores.*`
- [ ] `grep "lyra.adapters" src/lyra/config.py` returns empty
- [ ] `[importlinter:contract:commands-no-direct-infra]` and `[importlinter:contract:agents-no-direct-infra]` added and passing
- [ ] `uv run lint-imports` zero violations, zero exemptions for domain→infra paths
- [ ] Existing callers via shim re-exports all still resolve (no forced migration wave for external consumers)

### P2 — Hygiene (V10–V12)

- [ ] All files in `src/lyra/` satisfy `wc -l ≤ 300` (no new exemptions needed)
- [ ] `SimpleAgent` unit-testable without constructing real `VaultCli` or `WebIntelScraper`
- [ ] `uv run pytest` coverage unchanged or improved
