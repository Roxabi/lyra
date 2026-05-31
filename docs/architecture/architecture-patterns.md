# Architecture Patterns — Roxabi Standard

> Last updated: 2026-05-09

> **Status: REFERENCE**
> Scope: All Roxabi projects (lyra, voiceCLI, imageCLI, 2ndBrain, roxabi-plugins)
> Purpose: Define the architectural patterns and rules to follow

---

## Pattern Hierarchy

```
Clean Architecture (Martin)
    │
    └──▶ Hexagonal Architecture (Cockburn — Ports & Adapters)
              │
              └──▶ Kernel Architecture (Roxabi extension)
```

Each pattern **refines** the previous — adding structure, not replacing it.

---

## 1. Clean Architecture

### Principle

**Dependencies point inward.** The innermost layer (domain) has no dependencies on outer layers. Outer layers depend on inner layers, never the reverse.

### Layers

| Layer | Contains | Depends on |
|-------|----------|------------|
| **Domain** | Entities, business rules, pure logic | Nothing |
| **Application** | Use cases, orchestrators | Domain |
| **Infrastructure** | DB, external APIs, frameworks | Application |
| **Presentation** | UI, controllers, adapters | Application, Infrastructure |

### Invariant

```
Domain → nothing
Application → Domain only
Infrastructure → Application + Domain
Presentation → all
```

### Violation detection

- Import from outer layer in inner layer → ❌
- `from adapters import ...` in `core/` → ❌
- `from infrastructure import ...` in `domain/` → ❌

---

## 2. Hexagonal Architecture (Ports & Adapters)

### Principle

**Isolate the core from the world.** The domain is a hexagon; everything else plugs into it via ports (interfaces) and adapters (implementations).

### Structure

```
                    ┌─────────────────────┐
   INBOUND          │                     │          OUTBOUND
   ADAPTERS         │     DOMAIN CORE     │          ADAPTERS
                    │                     │
   Telegram ────────┤                     ├────────── LLM Provider
   Discord  ────────┤                     ├────────── Database
   HTTP     ────────┤                     ├────────── Message Queue
   CLI      ────────┤                     ├────────── External APIs
                    │                     │
                    └─────────────────────┘
                              │
                         PORTS (protocols)
```

### Port = Protocol

A **port** is an interface (Python `Protocol`) that the domain defines and depends on.

```python
# core/port.py
class LlmProvider(Protocol):
    async def complete(self, prompt: str) -> str: ...
    async def stream(self, prompt: str) -> AsyncIterator[str]: ...
```

### Adapter = Implementation

An **adapter** implements a port for a specific technology.

```python
# adapters/anthropic_adapter.py
class AnthropicAdapter(LlmProvider):
    async def complete(self, prompt: str) -> str:
        # Anthropic SDK call here
        ...
```

### Invariant

```
Domain defines ports → Ports are domain-owned
Adapters implement ports → Adapters are infrastructure-owned
Domain never imports adapters → Adapters import domain
```

### Inbound vs Outbound

| Type | Direction | Examples |
|------|-----------|----------|
| **Inbound** | External → Domain | Telegram, Discord, HTTP, CLI |
| **Outbound** | Domain → External | LLM, DB, Queue, Cache, API |

### Normalization Layer

Inbound adapters **normalize** external data into domain types:

```
raw_platform_event → normalize() → InboundMessage (domain type)
```

Outbound adapters **denormalize** domain types into platform calls:

```
OutboundMessage (domain type) → send() → platform_api_call
```

---

## 3. Kernel Architecture (Roxabi Extension)

### Principle

**The kernel is minimal, pure, and immutable.** It contains only the essence of the system — no frameworks, no I/O, no side effects. Everything else is a plugin.

### Structure

```
┌─────────────────────────────────────────────────────────────────┐
│                         PLUGINS                                 │
│   LLM Drivers │ Channels │ Storage │ Commands │ Skills │ Tools │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                         KERNEL                                  │
│   ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│   │  Protocols  │  │   Entities   │  │   Pure Functions     │  │
│   │  Ports      │  │   Events     │  │   Business Rules     │  │
│   └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│   • No framework imports                                        │
│   • No I/O                                                      │
│   • No side effects                                             │
│   • 100% testable in isolation                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Kernel constraints

| Constraint | Rationale |
|------------|-----------|
| No `aiogram`, `discord`, `anthropic`, `httpx` | Framework-agnostic |
| No `asyncio.open`, `open()`, network calls | I/O-free |
| No mutable global state | Pure functions |
| All types frozen (`frozen=True`) | Immutability |
| All functions pure (input → output) | Testability |

### Plugin constraints

| Constraint | Rationale |
|------------|-----------|
| Must implement a kernel-defined port | Contract enforcement |
| May have framework/I/O dependencies | Isolated side effects |
| Cannot import other plugins | Loose coupling |
| Communication via kernel events only | Decoupling |

### Event-driven communication

Plugins communicate through kernel-defined events:

```python
# kernel/events.py
@dataclass(frozen=True)
class InboundMessage:
    id: str
    text: str
    user_id: str
    platform: str
    ...

@dataclass(frozen=True)
class OutboundMessage:
    text: str
    reply_to: str | None
    ...
```

---

## Decision Matrix

When adding new code, ask:

| Question | Yes → | No → |
|----------|-------|------|
| Does it contain business logic? | Put in **kernel/core** | Put in **adapter/plugin** |
| Does it touch external systems? | Put in **adapter** | Can go in **core** |
| Does it depend on a framework? | Put in **adapter** | Can go in **core** |
| Is it a protocol/interface? | Define in **core** | N/A |
| Is it an implementation? | Put in **adapter** | N/A |

---

## File Placement Rules

```
src/lyra/
├── core/                    # KERNEL
│   ├── events.py            # frozen event types
│   ├── protocols.py         # Port definitions
│   ├── entities.py          # Domain entities
│   └── business_logic.py    # Pure functions
│
├── adapters/                # INBOUND/OUTBOUND ADAPTERS
│   ├── telegram.py          # Inbound + Outbound
│   ├── discord.py           # Inbound + Outbound
│   └── _shared.py           # Cross-adapter utilities
│
├── llm/                     # OUTBOUND ADAPTERS (LLM)
│   ├── base.py              # LlmProvider protocol
│   └── drivers/             # Concrete implementations
│
├── infrastructure/          # OUTBOUND ADAPTERS (Storage, per ADR-048)
│   └── stores/
│       └── sqlite_store.py  # DB implementation
│
└── commands/                # PLUGINS (commands/skills)
    └── vault_add.py         # Command implementation
```

---

## Testing Strategy

| Layer | Test Type | Tools |
|-------|-----------|-------|
| Kernel/Core | Unit tests, no mocks | pytest |
| Adapters | Integration tests with mocks | pytest + pytest-asyncio |
| Full system | End-to-end tests | pytest + real services |

**Kernel tests must:**
- Run with zero external dependencies
- Complete in < 100ms total
- Have 100% coverage of business logic

---

## Violations to Avoid

| Violation | Example | Fix |
|----------|---------|-----|
| Framework in core | `import aiogram` in `core/` | Move to adapter |
| I/O in core | `open()` in `core/` | Move to adapter |
| Mutable global | `state = {}` at module level | Use frozen dataclass + explicit state |
| Adapter imports adapter | `from telegram import ...` in `discord.py` | Communicate via events |
| Core imports adapter | `from adapters import ...` in `core/` | Define port in core, implement in adapter |

---

## Engineering Invariants (ADR-anchored)

> Concrete invariants from accepted ADRs that codify the patterns above. Each is enforceable by importlinter, code review, or test.

### Hexagonal canonical model

Four layers, innermost to outermost: **Domain** (entities, port protocols, business rules — zero I/O imports) → **Application** (use cases, command handlers — depends on Domain ports only) → **Infrastructure** (SQLite stores, NATS transport, model loaders — implements Domain ports; lives in `lyra.infrastructure.*` per ADR-048) → **Adapters** (Telegram, Discord, CLI, NATS adapters — outermost ring, never imported by inner layers).

The **CLI protocol circular import** (ADR-060, absorbed here) established the canonical fix shape: when a CLI protocol port was co-located with its Infrastructure importer, the solution was to define the port in `lyra.core` (Domain) and have Infrastructure import it from there. The **Composition Root** (`src/lyra/bootstrap/`) is the only site that wires concrete Infrastructure implementations to Domain ports. → ADR-059 (absorbs ADR-048, ADR-060)

### Typed error boundary

LyraUserError (defined in `lyra.core.exceptions`) is the base class for all errors that must produce a user-visible reply. Subclasses (AudioDownloadError, AudioTooLargeError, AudioInvalidFormatError, SttError) map to specific failure modes and carry a `key` for `MessageManager` template lookup plus a `fallback_text` for degraded mode.

ErrorBoundaryMiddleware sits at position 0 of the pipeline — it catches LyraUserError and any unhandled exception, dispatches a reply, and returns `_DROP`. It is a safety net for pipeline-internal failures; adapter-level download failures raise typed exceptions before the pipeline and are caught in the adapter's download function directly.

NullMessageManager replaces `if hub._msg_manager is None: return _DROP` guards, making misconfiguration observable instead of silently dropping messages. → ADR-058

### Generic error reply placement

`GENERIC_ERROR_REPLY` lives in `lyra.core.messaging.message`, co-located with the `Response` type it populates. It was moved from `lyra.core.hub` to break an agents → hub import coupling: `SimpleAgent` (a spoke) was importing a UI-primitive string from the hub coordinator. Since `message.py` is already a shared dependency with no upward coupling, all agents and the hub now import the constant from the same low-dependency module. The agents layer has no import dependency on `hub.py`. → ADR-009

### Invariants summary

- Domain layer imports nothing outside its own module (no I/O libs, no adapters, no infrastructure)
- Application orchestrates Domain via ports defined in Domain; it never imports Infrastructure concretions
- Infrastructure implements ports; lives in `lyra.infrastructure.*`; is the only layer that may hold migration runners and connection pools
- Adapters are the outer ring; never imported by inner layers; lateral adapter-to-adapter imports are forbidden
- All user-visible errors are LyraUserError subclasses raised at the point of failure
- All unhandled pipeline errors are caught at ErrorBoundaryMiddleware and translated into a user reply, never silently dropped
- Shared UI-primitive constants (`GENERIC_ERROR_REPLY`) live in `lyra.core.messaging.message`, not in hub or adapter modules
- Concrete implementations are instantiated only in the Composition Root (`lyra.bootstrap`); no factory that selects concretions may live in Domain or Application

### See also

- Storage layer (uses `lyra.infrastructure`) → `storage.md`
- Importlinter enforcement of these invariants → `workers-tooling.md` (ADR-061)

---

## Summary

| Pattern | Key Rule |
|---------|----------|
| Clean Architecture | Dependencies point inward |
| Hexagonal Architecture | Core isolated via ports/adapters |
| Kernel Architecture | Core is minimal, pure, immutable |

**All three apply simultaneously.** They are not alternatives — they are layers of the same onion.
