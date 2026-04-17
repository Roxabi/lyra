# Refactoring Strategy: Clean/Hexagonal + MicroKernel

> Created: 2026-04-16
> Status: Draft

---

## Current Architecture Assessment

### Ports (Protocols) — ✅ Well-defined

| Port | Purpose | Location |
|------|---------|----------|
| `ChannelAdapter` | Inbound/outbound platform ops | `hub/hub_protocol.py` |
| `PoolContext` | Pool → Hub seam | `pool/pool_context.py` |
| `Bus[T]` | Transport abstraction | `bus.py` |
| `Guard` | Auth rejection hook | `guard.py` |
| `AgentStoreProtocol` | Agent persistence | `stores/agent_store_protocol.py` |
| `LlmProvider` | LLM calls | `llm/base.py` + `agent_refiner.py` |
| `TtsProtocol` | TTS engine | `tts/__init__.py` |
| `STTProtocol` | STT engine | `stt/__init__.py` |
| `PipelineMiddleware` | Middleware pipeline | `hub/middleware.py` |
| `OutboundListener` | Outbound event handling | `outbound_listener.py` |

### Architecture Violations (to fix)

| Violation | Location | Issue |
|-----------|----------|-------|
| God Class | `hub.py` (791 lines) | Hub owns 6+ concerns |
| Infrastructure in Core | Rate limiting in `hub.py` | Should be middleware/plugin |
| Infrastructure in Core | TTS dispatch in `hub.py` | Should be port → adapter |
| Monolithic Bootstrap | `hub_standalone.py` | Should be plugin composition |

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         INFRASTRUCTURE LAYER                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Telegram │  │ Discord  │  │ SQLite   │  │ NATS     │  │ Ollama   │ │
│  │ Adapter  │  │ Adapter  │  │ Stores   │  │ Bus      │  │ Provider │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│       │             │             │             │             │        │
└───────┼─────────────┼─────────────┼─────────────┼─────────────┼────────┘
        │             │             │             │             │
        ▼             ▼             ▼             ▼             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              PORTS (Interfaces)                         │
│  ChannelAdapter  AgentStoreProtocol  Bus[T]  LlmProvider  TtsProtocol   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION LAYER                             │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │                     MicroKernel Core                                 ││
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            ││
│  │  │ HubCore  │  │ PoolMgr  │  │ Dispatch │  │ Bindings │            ││
│  │  │(router)  │  │(lifecycle│  │(routing) │  │(table)   │            ││
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            ││
│  │                         │                                           ││
│  │         ┌───────────────┼───────────────┐                          ││
│  │         ▼               ▼               ▼                          ││
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                    ││
│  │  │ Middleware │  │ Middleware │  │ Middleware │  ← Plugin Points   ││
│  │  │ RateLimit  │  │ TTSDispatch│  │ Logging    │                    ││
│  │  └────────────┘  └────────────┘  └────────────┘                    ││
│  └─────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                             DOMAIN LAYER                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ Agent    │  │ Pool     │  │ Message  │  │ Identity │  │ Trust    │  │
│  │ (entity) │  │ (entity) │  │ (entity) │  │ (entity) │  │ (enum)   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Refactoring Strategy by Layer

### 1. Domain Layer (Center) — Preserve Purity

**Files:** `message.py`, `identity.py`, `trust.py`, `agent.py` (entities only)

**Rule:** No infrastructure imports. Pure data structures + business rules.

**Current state:** ✅ Clean — no changes needed.

---

### 2. Application Layer — Extract HubCore

**Current `Hub` responsibilities:**

| Concern | Where it belongs | Extract to |
|---------|------------------|------------|
| Adapter registry | **MicroKernel Core** | `HubCore` (router) |
| Pool lifecycle | **MicroKernel Core** | `PoolManager` (already extracted) |
| Binding resolution | **MicroKernel Core** | `BindingTable` (new) |
| Rate limiting | **Plugin (middleware)** | `RateLimitMiddleware` |
| TTS dispatch | **Plugin (middleware)** | `TtsDispatchMiddleware` |
| Circuit breaker | **Plugin (middleware)** | `CircuitMiddleware` |
| Outbound routing | **MicroKernel Core** | `OutboundDispatcher` (already extracted) |

**Target `Hub` → `HubCore` (router only):**

```python
class HubCore:
    """Pure MicroKernel router — no business logic."""

    def __init__(self):
        self._adapters: dict[RoutingKey, ChannelAdapter] = {}
        self._bindings: BindingTable = BindingTable()
        self._pools: PoolManager = PoolManager()
        self._middleware: MiddlewarePipeline = MiddlewarePipeline()

    def register_adapter(self, key: RoutingKey, adapter: ChannelAdapter): ...
    def register_binding(self, key: RoutingKey, agent: str): ...
    async def dispatch(self, msg: InboundMessage) -> None:  # ← all logic via middleware
        await self._middleware.run(msg, context)
```

---

### 3. Middleware Plugin System — Standardize Extension Points

**Current middleware:** `PipelineMiddleware` protocol in `hub/middleware.py`

**Extend to cover all cross-cutting concerns:**

| Middleware | Priority | Current Location | Move to |
|------------|----------|------------------|--------|
| RateLimitMiddleware | 10 | `hub.py` methods | `hub/middleware/rate_limit.py` |
| CircuitMiddleware | 20 | `hub.py` methods | `hub/middleware/circuit.py` |
| BindingMiddleware | 30 | `hub.py` methods | `hub/middleware/binding.py` |
| PoolMiddleware | 40 | `hub.py` methods | `hub/middleware/pool.py` |
| TtsMiddleware | 50 | `hub.py` methods | `hub/middleware/tts.py` |
| LoggingMiddleware | 100 | (new) | `hub/middleware/logging.py` |

**Middleware contract:**

```python
class PipelineMiddleware(Protocol):
    async def process(
        self,
        msg: InboundMessage,
        context: PipelineContext,
        next: Callable[[], Coroutine[None, None, None]]
    ) -> None: ...
```

**Composition:**

```python
def build_default_pipeline() -> MiddlewarePipeline:
    return MiddlewarePipeline([
        RateLimitMiddleware(),
        CircuitMiddleware(),
        BindingMiddleware(),
        PoolMiddleware(),
        TtsMiddleware(),
        LoggingMiddleware(),
    ])
```

---

### 4. Infrastructure Layer — Enforce Port Boundaries

**Adapters (already clean):**

| Adapter | Implements | Status |
|---------|------------|--------|
| `TelegramAdapter` | `ChannelAdapter` | ✅ |
| `DiscordAdapter` | `ChannelAdapter` | ✅ |
| `CliAdapter` | `ChannelAdapter` | ✅ |

**Stores (already clean):**

| Store | Implements | Status |
|-------|------------|--------|
| `AgentStore` | `AgentStoreProtocol` | ✅ |
| `AuthStore` | (implicit port) | ⚠️ Define `AuthStoreProtocol` |
| `TurnStore` | (implicit port) | ⚠️ Define `TurnStoreProtocol` |

**Providers (already clean):**

| Provider | Implements | Status |
|----------|------------|--------|
| `OllamaProvider` | `LlmProvider` | ✅ |
| `VoiceCliTTS` | `TtsProtocol` | ✅ |
| `VoiceCliSTT` | `STTProtocol` | ✅ |

---

### 5. Bootstrap — MicroKernel Plugin Composition

**Current:** Monolithic `_bootstrap_hub_standalone()` function.

**Target:** Plugin-based composition:

```python
# bootstrap/plugins/registry.py
BOOTSTRAP_PLUGINS = [
    ("stores", StoresPlugin),
    ("auth", AuthPlugin),
    ("llm", LlmPlugin),
    ("tts", TtsPlugin),
    ("stt", SttPlugin),
    ("adapters", AdaptersPlugin),
    ("health", HealthPlugin),
]

# bootstrap/hub_standalone.py
async def _bootstrap_hub_standalone():
    registry = PluginRegistry(BOOTSTRAP_PLUGINS)

    hub = HubCore()
    await registry.run_all("init", hub, config)  # Plugin lifecycle hook
    await registry.run_all("wire", hub, config)
    await registry.run_all("start", hub, config)

    return hub
```

**Each plugin implements:**

```python
class BootstrapPlugin(Protocol):
    async def init(self, hub: HubCore, config: Config) -> None: ...
    async def wire(self, hub: HubCore, config: Config) -> None: ...
    async def start(self, hub: HubCore, config: Config) -> None: ...
    async def stop(self, hub: HubCore) -> None: ...
```

---

## File Extraction Map

| Current File | Lines | Extract To | Target Lines |
|--------------|-------|------------|--------------|
| `hub/hub.py` | 791 | `hub/core.py` + middleware modules | ~150 (core) + 5×~50 (middleware) |
| `bootstrap/hub_standalone.py` | 432 | `bootstrap/plugins/*.py` | ~80 (orchestration) |
| `adapters/_shared.py` | 432 | `_shared_*.py` (continue extraction) | ~50 (re-exports) |
| `core/cli_pool.py` | 430 | Verify mixin balance | (check) |
| `adapters/discord.py` | 322 | Already modular | ✅ |

---

## Architectural Rules

### Direction Rule
Dependencies point INWARD: Infrastructure → Ports → Application → Domain.
**NEVER:** Domain imports Application/Infrastructure.

### Port Rule
All cross-layer communication via Protocols (structural typing).
**NEVER:** Concrete class imports across layer boundaries.

### Middleware Rule
All cross-cutting concerns (rate limiting, circuit breaker, TTS, logging) are middleware.
**NEVER:** Business logic in HubCore — HubCore is pure routing.

### Plugin Rule
All bootstrap concerns (stores, LLM, TTS, adapters) are plugins.
**NEVER:** Monolithic bootstrap function — compose from plugins.

### Test Rule
Domain/Application tested via Protocol mocks.
**NEVER:** Infrastructure required for unit tests.

---

## Priority Order

| Priority | Action | Architectural Fix |
|----------|--------|-------------------|
| **P1** | Extract `HubCore` from `hub.py` | God Class → MicroKernel router |
| **P2** | Convert rate limiting to middleware | Infrastructure leak → Plugin |
| **P3** | Convert TTS dispatch to middleware | Infrastructure leak → Plugin |
| **P4** | Define missing port protocols (`AuthStoreProtocol`, etc.) | Implicit coupling → Explicit port |
| **P5** | Plugin-ify bootstrap | Monolith → Composable |

---

## GitNexus Impact Analysis

> Analysis date: 2026-04-16
> Index status: Stale (2 commits behind HEAD)

### Index Stats

| Metric | Value |
|--------|-------|
| Files indexed | 1,051 |
| Nodes | 16,291 |
| Edges | 37,698 |
| Communities | 451 |
| Processes | 300 |

### Blast Radius

| Depth | Count | Type |
|-------|-------|------|
| **d=1 (WILL BREAK)** | 24 src files, 138 calls | Direct callers |
| **d=2+ (LIKELY AFFECTED)** | 300 processes, 54 test files | Indirect deps |
| **Test surface** | 967 test occurrences | Test coverage |

### Risk Level: HIGH

| Factor | Assessment | Details |
|--------|------------|---------|
| Direct callers | HIGH | 24 source files with 138 hub.* calls |
| Test surface | VERY HIGH | 54 test files, 967 test occurrences |
| Critical path | CRITICAL | Hub is on the hot path for every inbound message |
| Process dependencies | HIGH | 300 indexed processes depend on Hub |

### Direct Callers (d=1) — Key Files

| Caller Module | Hub Methods Used | Calls |
|---------------|------------------|-------|
| `hub/middleware_stages.py` | `_resolve_message_trust`, `_is_rate_limited`, `resolve_binding`, `get_or_create_pool` | 9 |
| `hub/middleware_stt.py` | `_msg_manager`, `_stt`, `dispatch_response` | 9 |
| `hub/pool_manager.py` | `_debounce_ms`, `_turn_timeout`, `_max_sdk_history`, `_pool_ttl`, etc. | 20 |
| `core/tts_dispatch.py` | `_tts`, `_prefs_store`, `dispatch_audio`, `_route_outbound` | 7 |
| `bootstrap/hub_standalone.py` | `register_agent`, `register_authenticator`, `register_adapter`, `register_binding` | 24 |
| `bootstrap/bootstrap_wiring.py` | `register_authenticator`, `register_adapter`, `resolve_identity` | 13 |
| `bootstrap/health.py` | `_start_time`, `_last_processed_at` | 14 |

### Already Extracted (Post-#294)

| Module | Lines | Status |
|--------|-------|--------|
| `hub/pool_manager.py` | 118 | ✅ Extracted |
| `core/tts_dispatch.py` | 242 | ✅ Extracted |
| `hub/middleware*.py` | 450+ | ✅ Extracted |
| `hub/outbound_dispatcher.py` | 224 | ✅ Extracted |

### Extraction Risk by Target

| Target | Risk | Lines | Callers | Rationale |
|--------|------|-------|---------|-----------|
| Identity resolution | **LOW** | ~90 | 4 | Pure functions, no I/O |
| TTS helpers | **MEDIUM** | ~40 | 5 | Cohesive with `tts_dispatch.py` |
| Outbound routing | **MEDIUM-HIGH** | ~250 | 12 | Core dispatch path |
| Rate limiting wrappers | **LOW VALUE** | ~20 | 2 | Already delegated to `RateLimiter` |

---

## Detailed Extraction Plan

### Phase 1: Identity Resolution (LOW RISK)

**Target:** Extract `resolve_identity`, `resolve_binding`, `_resolve_message_trust` to dedicated module.

**Rationale:** Pure functions with no side effects. Only 4 callers. Good extraction candidate.

**Files to modify:**

| File | Action |
|------|--------|
| `hub/hub.py` | Remove methods, delegate to new module |
| `hub/identity_resolver.py` | **NEW** — extracted methods |
| `hub/middleware_stages.py` | Update calls to use resolver |
| `bootstrap/bootstrap_wiring.py` | Update `resolve_identity` call |

**Estimated LOC change:**
- `hub.py`: -90 lines
- New `identity_resolver.py`: +100 lines (including protocol/class wrapper)

**Test impact:** Low — existing tests mock Hub, no structural change needed.

---

### Phase 2: TTS Helpers (MEDIUM RISK)

**Target:** Move TTS-related helper methods to `tts_dispatch.py`.

**Methods:**
- `_resolve_agent_tts(msg)` → resolves TTS config for agent
- `_tts_language_kwargs(msg)` → builds language kwargs for TTS
- `_resolve_agent_fallback_language(msg)` → fallback language resolution

**Rationale:** Already cohesive with `AudioPipeline` in `tts_dispatch.py`. 5 callers all internal to dispatch path.

**Files to modify:**

| File | Action |
|------|--------|
| `hub/hub.py` | Remove TTS helper methods |
| `core/tts_dispatch.py` | Add helper methods, integrate with `AudioPipeline` |
| `hub/middleware_stages.py` | Update any direct calls |

**Estimated LOC change:**
- `hub.py`: -40 lines
- `tts_dispatch.py`: +50 lines

**Test impact:** Medium — TTS-related tests may need import updates.

---

### Phase 3: Outbound Routing Refactor (MEDIUM-HIGH RISK)

**Target:** Consolidate outbound routing logic.

**Current state:** `_route_outbound` and 6 `dispatch_*` methods live in `hub.py`. These are the core dispatch path.

**Options:**

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A: Extend `OutboundDispatcher` | Move dispatch methods to existing dispatcher class | Minimal new files | Dispatcher becomes larger |
| B: Create `OutboundRouter` | New class for routing logic | Clear separation | More indirection |
| C: Keep in Hub | Status quo | No refactoring | Hub remains 250+ lines for dispatch |

**Recommendation:** Option A — extend `OutboundDispatcher` since it already handles outbound logic.

**Files to modify:**

| File | Action |
|------|--------|
| `hub/hub.py` | Remove dispatch methods, delegate to dispatcher |
| `hub/outbound_dispatcher.py` | Add `dispatch_response`, `dispatch_streaming`, `dispatch_attachment`, `dispatch_audio*` |
| `core/tts_dispatch.py` | Update to use dispatcher directly |
| `hub/middleware_stages.py` | Update dispatch calls |

**Estimated LOC change:**
- `hub.py`: -250 lines
- `outbound_dispatcher.py`: +270 lines

**Test impact:** High — many tests touch dispatch methods. Need careful migration.

---

### Phase 4: Rate Limiting (OPTIONAL — LOW VALUE)

**Target:** Consider removing thin wrapper methods.

**Current state:**
- `RateLimiter` class exists in `hub/hub_rate_limit.py`
- Hub has `_is_rate_limited()` and `_is_rate_limited_by_key()` that delegate
- Only 2 callers

**Recommendation:** **SKIP** — wrappers are ~20 lines, already delegated. Not worth indirection cost.

---

### Phase 5: Bootstrap Plugin-ification (DEFERRED)

**Target:** Convert monolithic `_bootstrap_hub_standalone()` to plugin composition.

**Rationale:** Architectural improvement, but Hub extraction is higher priority.

**Status:** Deferred until Phase 1-3 complete.

---

## Execution Roadmap

| Sprint | Phase | Risk | LOC Delta | Test Effort |
|--------|-------|------|-----------|-------------|
| 1 | Identity Resolution | LOW | -90 / +100 | Low |
| 1 | TTS Helpers | MEDIUM | -40 / +50 | Medium |
| 2 | Outbound Routing | MEDIUM-HIGH | -250 / +270 | High |
| — | Rate Limiting | LOW VALUE | Skip | — |
| 3 | Bootstrap Plugins | MEDIUM | TBD | Medium |

**Total estimated reduction in `hub.py`:** ~380 lines → ~410 lines remaining

---

## Success Criteria

- [ ] All tests pass after each phase
- [ ] GitNexus `detect_changes` shows expected scope only
- [ ] No new circular imports
- [ ] Hub line count < 500 after Phase 3
- [ ] All dispatch tests migrated to `OutboundDispatcher` tests
