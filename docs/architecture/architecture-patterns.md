# Architecture Patterns — Roxabi Standard

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
├── stores/                  # OUTBOUND ADAPTERS (Storage)
│   └── sqlite_store.py      # DB implementation
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

## Summary

| Pattern | Key Rule |
|---------|----------|
| Clean Architecture | Dependencies point inward |
| Hexagonal Architecture | Core isolated via ports/adapters |
| Kernel Architecture | Core is minimal, pure, immutable |

**All three apply simultaneously.** They are not alternatives — they are layers of the same onion.
