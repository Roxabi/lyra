# Chimera Strategy — Best-of-Each for Lyra

> Competitive analysis of 5 AI agent engines (OpenClaw, IronClaw, OpenFang, Nanobot, NanoClaw) distilled into a prioritized adoption roadmap for Lyra.

## Repos Analyzed

| Project | Language | Size | Strengths |
|---------|----------|------|-----------|
| [OpenClaw](https://github.com/openclaw/openclaw) | TypeScript | ~2,850 files, 39MB | Memory, plugins, events, observability, config |
| [IronClaw](https://github.com/nearai/ironclaw) | Rust | 99K LOC, 300+ files | LLM abstraction, tools, security, WASM sandbox |
| [OpenFang](https://github.com/RightNow-AI/openfang) | Rust | 137K LOC, 14 crates | Channels (40), orchestration, security (16 layers) |
| [Nanobot](https://github.com/HKUDS/nanobot) | Python | 4K LOC, 83 files | Readability, provider registry, simplicity |
| [NanoClaw](https://github.com/qwibitai/nanoclaw) | TypeScript | 7K LOC | Container isolation, credential proxy, security |

Visual plans for each: `~/.agent/diagrams/{name}-architecture-plan.html`
Comparison matrix: `~/.agent/diagrams/clawfamily-comparison-matrix.html`
Cloned repos: `~/projects/clawfamily/`

---

## Maturity Matrix

Scale: 0 (absent) → 5 (production-grade). **Bold** = best in class.

| Capability | OpenClaw | IronClaw | OpenFang | Nanobot | NanoClaw | Lyra |
|------------|----------|----------|----------|---------|----------|------|
| Memory — Storage | **5** | **5** | 4 | 1 | 2 | 2 |
| Memory — Search | **5** | **5** | 4 | 0 | 0 | 1 |
| Memory — Context Mgmt | **5** | 4 | 4 | 3 | 1 | 2 |
| Channel Count | 4 | 3 | **5** | 4 | 2 | 2 |
| Channel Abstraction | **5** | 4 | 4 | 3 | 3 | 3 |
| Bus / Event System | **5** | 3 | **5** | 2 | 1 | 2 |
| LLM Providers | 4 | **5** | **5** | 4 | 1 | 3 |
| Tool System | 3 | **5** | **5** | 3 | 2 | 2 |
| Plugin System | **5** | 4 | 3 | 2 | 1 | 2 |
| Security | 3 | **5** | **5** | 2 | 4 | 3 |
| Multi-Agent / Orchestration | 3 | 4 | **5** | 2 | 2 | 0 |
| Observability | **5** | 4 | 3 | 2 | 1 | 2 |
| Config / Hot-Reload | **5** | 4 | 4 | 3 | 2 | 4 |
| Codebase Readability | 2 | 3 | 2 | **5** | 4 | **5** |

**Key insight**: No single project dominates all categories. The sweet spot for Lyra: maintain readability (5/5) while cherry-picking patterns from the leaders.

---

## Winner per Category

| Capability | Winner | What to Adopt |
|------------|--------|---------------|
| Memory Storage | OpenClaw + IronClaw | SQLite + FTS5 + optional vectors. Hybrid RRF search. 800-word chunks with 15% overlap. |
| Context Engine | OpenClaw | Pluggable `ContextEngine` protocol: `assemble()` (token-budget), `compact()` (summarize/truncate), `after_turn()` lifecycle. |
| Channel Abstraction | OpenClaw | 7-tier binding resolution (peer → parent → guild → team → account → channel). 4-part adapter pattern (Monitor/Context/Handler/Sender). |
| Channel Scale | OpenFang | `ChannelBridgeHandle` trait pattern preventing circular deps between kernel and channels. |
| Event System | OpenClaw + OpenFang | Multi-stream events (agent/diagnostic/heartbeat). EventBus with 1000-entry history ring buffer. Per-agent channels for scale. |
| LLM Providers | IronClaw | Decorator chain: Base → Retry → CircuitBreaker → Failover → Cached → SmartRouting. Each wraps the next. |
| Provider Detection | Nanobot | Metadata-driven `ProviderSpec` with api_key prefix matching, env var detection. No if-elif chains. |
| Tool System | IronClaw + OpenFang | Tool trait with `approval_requirement` (Never/UnlessAutoApproved/Always). Capability-based enforcement. |
| Plugin System | OpenClaw | Typed extension points (tools, hooks, channels, providers). Per-plugin API factory with access control. |
| Security | OpenFang + NanoClaw | OpenFang: taint tracking, prompt injection scanner, SSRF protection. NanoClaw: credential proxy (agents never see real API keys). |
| Concurrency | OpenClaw | Lane-based command queue: named lanes (main, cron, heartbeat) with per-lane concurrency + generation tracking. |
| Orchestration | OpenFang | Workflow engine: Sequential/FanOut/Collect/Conditional/Loop steps. Trigger engine (event pattern, cron, approval gates). |
| Observability | OpenClaw | Diagnostic events: stuck-session detection, tool-loop detection, token usage tracking. OTEL-ready. |

---

## Critical Constraint

Lyra's advantage is its **~300-line hub** readable in an afternoon. Every pattern adopted must:
- Be adapted to Python asyncio idioms
- Stay minimal (implement the interface, not the full feature set)
- Not bloat the core — new capabilities live in separate modules

**Goal: 80% of the capability at 10% of the code.**

---

## Prioritized Adoption Roadmap

### Phase 1b — Foundation (~550 LOC)

High impact, self-contained modules that don't require touching `hub.py`.

#### 1. Provider Registry (~80 LOC)
**Source**: Nanobot `nanobot/providers/registry.py`

Metadata-driven auto-detection instead of if-elif chains.

```python
@dataclass(frozen=True)
class ProviderSpec:
    name: str
    env_vars: list[str]        # ["ANTHROPIC_API_KEY"]
    api_key_prefix: str        # "sk-ant-"
    base_url_keyword: str      # "anthropic"
    litellm_prefix: str        # "anthropic/"

class ProviderRegistry:
    def __init__(self, specs: list[ProviderSpec]): ...
    def resolve(self) -> LlmProvider:
        """Auto-detect best provider from environment."""
```

#### 2. LLM Decorator Chain (~150 LOC)
**Source**: IronClaw `src/providers/decorators/`

Refactor existing `circuit_breaker.py` as a composable decorator. Each decorator wraps and delegates.

```python
class LlmProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

class RetryDecorator(LlmProvider):
    def __init__(self, inner: LlmProvider, max_retries: int = 3): ...

class CircuitBreakerDecorator(LlmProvider):
    def __init__(self, inner: LlmProvider, threshold: int = 5): ...

class CachedDecorator(LlmProvider):
    def __init__(self, inner: LlmProvider, ttl: int = 300): ...

# Composable stack:
provider = AnthropicProvider(api_key=...)
provider = RetryDecorator(provider)
provider = CircuitBreakerDecorator(provider)
```

#### 3. Hybrid RRF Search (~200 LOC)
**Source**: OpenClaw `src/memory/search-manager.ts` + IronClaw `src/workspace/`

Add FTS5 virtual table to `roxabi_memory`. Implement Reciprocal Rank Fusion.

```python
async def hybrid_search(query: str, limit: int = 10) -> list[SearchResult]:
    fts_results = await fts_search(query)           # always available
    vec_results = await vector_search(query)         # None if no embeddings
    return reciprocal_rank_fusion(fts_results, vec_results, k=60)

def reciprocal_rank_fusion(
    fts: list[SearchResult],
    vec: list[SearchResult] | None,
    k: int = 60
) -> list[SearchResult]:
    scores: dict[str, float] = {}
    for rank, r in enumerate(fts):
        scores[r.chunk_id] = scores.get(r.chunk_id, 0) + 1 / (k + rank)
    if vec:
        for rank, r in enumerate(vec):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0) + 1 / (k + rank)
    return sorted(results, key=lambda r: scores[r.chunk_id], reverse=True)
```

#### 4. ContextEngine Protocol (~120 LOC)
**Source**: OpenClaw `src/context-engine/types.ts`

Pluggable strategy for assembling LLM context within a token budget.

```python
class ContextEngine(Protocol):
    async def assemble(self, budget_tokens: int) -> AssembleResult: ...
    async def compact(self, messages: list[Message]) -> CompactResult: ...
    async def after_turn(self, turn: Turn) -> None: ...

@dataclass
class AssembleResult:
    messages: list[dict]
    estimated_tokens: int
    system_prompt_addition: str | None = None

@dataclass
class CompactResult:
    ok: bool
    compacted: bool
    tokens_before: int
    tokens_after: int
    summary: str | None = None
```

Default implementation: truncate oldest messages. Phase 2: LLM-summarized compaction.

---

### Phase 2 — Production Readiness (~610 LOC)

#### 5. Lane-Based Queue (~100 LOC)
**Source**: OpenClaw `src/process/command-queue.ts`

Replace single `asyncio.Queue` with named lanes.

```python
class LaneQueue:
    def __init__(self):
        self.lanes: dict[str, asyncio.Queue] = {
            "main": asyncio.Queue(maxsize=100),
            "cron": asyncio.Queue(maxsize=50),
            "heartbeat": asyncio.Queue(maxsize=10),
        }
        self.concurrency: dict[str, int] = {"main": 1, "cron": 1, "heartbeat": 1}

    async def enqueue(self, lane: str, task: Callable) -> Any: ...
```

Cron and heartbeat tasks stop blocking user messages.

#### 6. Binding Resolution Tiers (~80 LOC)
**Source**: OpenClaw `src/routing/resolve-route.ts`

Upgrade from exact-key + wildcard to graduated resolution.

```python
RESOLUTION_ORDER = [
    "peer",           # Direct DM / specific channel match
    "peer.parent",    # Thread parent inheritance
    "guild+roles",    # Discord guild + member roles
    "guild",          # Discord server constraint
    "team",           # Slack / Google Chat team
    "account",        # Account-scoped default
    "channel",        # Fallback to channel default
]
```

#### 7. Tool Approval Levels (~60 LOC)
**Source**: IronClaw `src/tools/types.rs`

```python
class ApprovalRequirement(Enum):
    NEVER = "never"                          # No approval needed
    UNLESS_AUTO_APPROVED = "unless_auto"     # Needs approval unless session auto-approved
    ALWAYS = "always"                        # Always needs explicit approval

@dataclass
class ToolMeta:
    name: str
    approval: ApprovalRequirement = ApprovalRequirement.NEVER
```

#### 8. Credential Proxy (~120 LOC)
**Source**: NanoClaw `src/credential-proxy.ts`

HTTP proxy on localhost injects real API keys. Agents connect to proxy URL.

```python
class CredentialProxy:
    """HTTP proxy that injects real API keys into forwarded requests."""
    def __init__(self, port: int = 3001):
        self.real_keys: dict[str, str] = {}  # loaded from env

    async def handle_request(self, request: Request) -> Response:
        # Inject x-api-key header, forward to upstream
```

#### 9. Diagnostic Events (~150 LOC)
**Source**: OpenClaw `src/infra/diagnostic-events.ts`

Start with 5 event types, expand later.

```python
class DiagnosticEvent(Enum):
    TOKEN_USAGE = "token_usage"
    SESSION_STUCK = "session_stuck"
    TOOL_LOOP = "tool_loop"
    MESSAGE_PROCESSED = "message_processed"
    PROVIDER_ERROR = "provider_error"

class DiagnosticBus:
    def emit(self, event: DiagnosticEvent, data: dict) -> None: ...
    def on(self, event: DiagnosticEvent, handler: Callable) -> Callable: ...
```

#### 10. Prompt Injection Scanner (~100 LOC)
**Source**: OpenFang `openfang-runtime/src/safety/`

Pattern-based detection with severity levels.

```python
class Severity(Enum):
    BLOCK = "block"
    WARN = "warn"
    SANITIZE = "sanitize"

PATTERNS = [
    (r"ignore\s+(previous|above|all)\s+instructions", Severity.BLOCK),
    (r"system\s*prompt", Severity.WARN),
    (r"<script|javascript:", Severity.SANITIZE),
]

def scan(content: str) -> list[Finding]: ...
```

---

### Phase 3 — Autonomy (~700 LOC)

| # | Pattern | Source | ~LOC |
|---|---------|--------|------|
| 11 | Typed Plugin SDK (tools, hooks, channels extension points) | OpenClaw | ~300 |
| 12 | Workflow Engine (Sequential/FanOut/Collect with triggers) | OpenFang | ~400 |

---

## Total Budget

| Phase | LOC | Capabilities Added |
|-------|-----|--------------------|
| **1b** | ~550 | Provider registry, decorator chain, hybrid search, context engine |
| **2** | ~610 | Lane queue, binding tiers, tool approval, credential proxy, diagnostics, injection scanner |
| **3** | ~700 | Typed plugin SDK, workflow engine |
| **Total** | **~1,860** | 12 major capabilities from 5 projects |

For reference, the projects these patterns come from have:
- OpenFang: 137,000 LOC
- IronClaw: 99,000 LOC
- OpenClaw: ~150,000 LOC (estimated from 39MB)
- Nanobot: 4,000 LOC
- NanoClaw: 7,000 LOC

**Lyra's target: ~2,200 total LOC (current ~300 hub + ~1,860 new) for equivalent capability.** That's 1.5% of OpenFang's codebase.

---

## Key Architectural Decisions

### What NOT to adopt

| Pattern | Project | Why Skip |
|---------|---------|----------|
| WASM tool sandboxing | IronClaw | Docker containers (Phase 3) simpler for Python; WASM is Rust-native |
| 40 channel adapters | OpenFang | Start with 3-4, add on demand. Most users need Telegram + Discord |
| npm plugin ecosystem | OpenClaw | Python ecosystem has different patterns; PyPI + TOML manifests better fit |
| Dual-database (Postgres + libSQL) | IronClaw | SQLite with WAL mode covers personal use. Add Postgres only if needed |
| Three-dot window chrome | All | It's a cliché. Don't do it. |

### What makes Lyra unique

1. **Two-machine architecture** — no other project splits hub from GPU server
2. **~300-line hub** — every other project has 10-100x more core code
3. **Python asyncio** — same ecosystem as the AI libraries it wraps
4. **TOML hot-reload** — change config without restart (matched only by OpenClaw)
5. **Explicit tool allowlist** — security by default (no Bash/Write unless declared)

---

## Reference Files

### Claw Family source code
```
~/projects/clawfamily/
├── openclaw/      # TypeScript, 39MB
├── ironclaw/      # Rust, 99K LOC
├── openfang/      # Rust, 137K LOC
├── nanobot/       # Python, 4K LOC
└── nanoclaw/      # TypeScript, 7K LOC
```

### Visual architecture plans
```
~/.agent/diagrams/
├── openclaw-architecture-plan.html
├── ironclaw-architecture-plan.html
├── openfang-architecture-plan.html
├── nanobot-architecture-plan.html
├── nanoclaw-architecture-plan.html
├── lyra-architecture-plan.html
└── clawfamily-comparison-matrix.html
```

### Key source files to study per pattern

| Pattern | File to read |
|---------|-------------|
| Provider Registry | `~/projects/clawfamily/nanobot/nanobot/providers/registry.py` |
| Decorator Chain | `~/projects/clawfamily/ironclaw/src/providers/` |
| Hybrid RRF Search | `~/projects/clawfamily/openclaw/src/memory/search-manager.ts` |
| ContextEngine | `~/projects/clawfamily/openclaw/src/context-engine/types.ts` |
| Lane Queue | `~/projects/clawfamily/openclaw/src/process/command-queue.ts` |
| Binding Tiers | `~/projects/clawfamily/openclaw/src/routing/resolve-route.ts` |
| Tool Approval | `~/projects/clawfamily/ironclaw/src/tools/types.rs` |
| Credential Proxy | `~/projects/clawfamily/nanoclaw/src/credential-proxy.ts` |
| Diagnostic Events | `~/projects/clawfamily/openclaw/src/infra/diagnostic-events.ts` |
| Injection Scanner | `~/projects/clawfamily/openfang/openfang-runtime/src/safety/` |
| EventBus + History | `~/projects/clawfamily/openfang/openfang-kernel/src/bus/` |
| Workflow Engine | `~/projects/clawfamily/openfang/openfang-kernel/src/workflow/` |
