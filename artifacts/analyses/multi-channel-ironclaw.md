# IronClaw — Multi-Channel Architecture Analysis

> Source: /home/mickael/projects/clawfamily/ironclaw
> Analyzed: 2026-03-09
> Relevance: HIGH — Best-in-class LLM resilience, security, and tool patterns

## What it is

Rust-native, security-first personal AI agent engine. Hub-and-spoke architecture with defense-in-depth against data exfiltration and prompt injection. Tokio async runtime (multi-threaded). PostgreSQL 15+ with pgvector for storage.

## Channel Support

5 channel types, all converge via `futures::StreamExt::select_all` into the agent main loop:

| Channel | Implementation | Notes |
|---------|---------------|-------|
| REPL | `ReplChannel` | Interactive terminal CLI |
| HTTP/Webhooks | `HttpChannel` + `WebhookServer` | REST API + webhook receivers |
| WASM Channels | `WasmChannelRuntime` | Telegram, Slack, Discord, WhatsApp via WASM adapters |
| Web Gateway | `GatewayChannel` (Axum) | Browser UI with SSE/WebSocket streaming |
| Signal | `SignalChannel` | SIGTERM/SIGINT handling |

**WASM Channel Pattern** (`src/channels/wasm/mod.rs`): Fresh WASM instance per callback — no shared mutable state. Host (Rust) manages HTTP routing, polling scheduler, timers. WASM exports `on_http_req`, `on_poll`, `on_respond`. Host injects credentials at boundary.

## LLM Decorator Chain (Composable Wrappers)

`src/llm/mod.rs` — `build_provider_chain()` lines 367–529. Decorators stack at build time, not request time:

```
Raw Provider
  ↓ RetryProvider         — exponential backoff ±25% jitter, retryable error classification
  ↓ SmartRoutingProvider  — cheap model for simple queries, primary for complex
  ↓ FailoverProvider      — vector of providers, cooldown after N failures
  ↓ CircuitBreakerProvider — Closed→Open→HalfOpen state machine
  ↓ CachedProvider        — SHA-256 keyed, never caches tool calls, LRU+TTL
  ↓ RecordingLlm          — optional trace recording
Final LlmProvider
```

All implement `LlmProvider` trait — async-transparent, each wrapper is <10 lines per method.

### RetryProvider (`src/llm/retry.rs`)
- Exponential backoff: 1s → 2s → 4s with ±25% jitter
- **Retryable**: RequestFailed, RateLimited, InvalidResponse, Io
- **Non-retryable**: AuthFailed, ContextLengthExceeded, SessionExpired, ModelNotAvailable
- Respects `retry_after` hint from RateLimited errors

### CircuitBreakerProvider (`src/llm/circuit_breaker.rs`)
- States: Closed → (failures ≥ threshold) → Open → (recovery timeout) → HalfOpen → (success) → Closed
- Blocks calls when Open, allows probes in HalfOpen
- Tracks consecutive transient failures

### CachedProvider (`src/llm/response_cache.rs`)
- Key: SHA-256(model + messages)
- **Never caches** `complete_with_tools()` (side effects)
- Hit rate logged every 100 requests

## Tool Approval Levels

`src/tools/tool.rs` lines 12–85:

```rust
pub enum ApprovalRequirement {
    Never,               // Read-only: echo, time, json, read_file, memory_search
    UnlessAutoApproved,  // Pre-approvable per session: http, job ops, skill management
    Always,              // Destructive: shell with rm -rf, chmod 777, DROP TABLE
}
```

Shell tool (`src/tools/builtin/shell.rs`) has static `NEVER_AUTO_APPROVE_PATTERNS`:
`rm -rf`, `chmod 777`, `crontab`, `git push --force`, `DROP TABLE`, `DELETE FROM`, `reboot`, `init 0`...

These are blocked from auto-approval even if shell is globally approved for a session.

## Credential Proxy

`src/tools/wasm/credential_injector.rs` (641 lines). WASM code **never receives decrypted secret values**.

```
WASM requests HTTP
  → Host receives request
  → Match credential by host pattern (wildcards: *.github.com)
  → Decrypt from PostgreSQL (AES-256-GCM, HKDF-derived keys)
  → Inject at boundary:
      AuthorizationBearer: "Authorization: Bearer {secret}"
      AuthorizationBasic: "Authorization: Basic {base64}"
      Header: "X-API-Key: {secret}"
      QueryParam: ?key={secret}
  → Execute HTTP request
  → Scan response for secret exfiltration
  → Return to WASM (redacted if leak detected)
```

`SharedCredentialRegistry`: thread-safe, append-only, `std::sync::RwLock`.

## Hybrid RRF Search

`src/workspace/search.rs` — Reciprocal Rank Fusion of FTS + vector:

```
1. FTS query  → top 50 results (PostgreSQL ts_rank_cd)
2. Vector query → top 50 results (pgvector cosine similarity)
3. For each result: rrf_score += 1.0 / (k + rank)  [k=60 default]
4. Normalize to [0,1], sort, return top N
```

`SearchResult` tracks `fts_rank` and `vector_rank` separately — allows analysis of which method contributed. Configurable: `use_fts`, `use_vector` toggles; `min_score` filter; `pre_fusion_limit`.

## Provider Registry

`src/llm/registry.rs` + `providers.json` (80+ entries). Metadata-driven — adding a new OpenAI-compatible provider requires zero Rust code:

```json
{
  "id": "groq",
  "aliases": ["groq-api"],
  "protocol": "open_ai_completions",
  "api_key_env": "GROQ_API_KEY",
  "default_model": "mixtral-8x7b-32768",
  "setup": { "kind": "api_key", "can_list_models": true }
}
```

Load order: built-in `providers.json` (compiled-in) → user overrides from `~/.ironclaw/providers.json`. Env var resolution: `{api_key_env}` → `{model_env}` → `{base_url_env}`.

Built-in providers: OpenAI, Anthropic, Ollama, Groq, OpenRouter, Together AI, Fireworks, NVIDIA NIM, Hugging Face, vLLM, and more.

## Concurrency Model

Tokio multi-threaded runtime. All channels feed `select_all()` — first to emit wins. Agent main loop:

```
channels.select_all().next()
  → router.classify_intent() → Command | Task | Query
  → Task:  scheduler.spawn_job() via tokio::spawn()
  → Query: dispatcher.run_agentic_loop()
  → Command: handle_system_command()
```

Scheduler enforces `max_parallel_jobs`. Each job gets isolated `JobContext`. No global state. `Arc<Mutex<T>>` / `Arc<RwLock<T>>` for shared state. Atomic counters for stats (lock-free).

## Memory / Storage

3 layers:
- **Short-term**: `JobContext` in-memory (current job variables, tool results, LLM messages)
- **Session**: `SessionManager::RwLock<HashMap<session_id, Thread>>` — turn history, approval state, cost tracking
- **Persistent**: PostgreSQL — secrets (AES-256-GCM encrypted), messages, documents+chunks+embeddings, jobs, routines

Workspace (`src/workspace/`): document storage with path-based access, recursive semantic chunking, hybrid FTS+vector search.

## Security Model (7 Layers)

```
WASM → Allowlist → Leak Scan (req) → Credential Injector → Execute → Leak Scan (resp) → WASM
```

1. **WASM Sandbox** — Wasmtime, fuel metering, memory caps, fresh instance per call
2. **Endpoint Allowlist** — tool declares allowed HTTP hosts, validated at host boundary
3. **Request Leak Detection** — scans outgoing request for secret values before sending
4. **Credential Injection** — secrets injected at boundary, never in WASM
5. **Rate Limiting** — per-tool: 60/min, 1000/hour, token bucket algorithm
6. **Prompt Injection Defense** — pattern-based detection, severity levels (Block/Warn/Review/Sanitize)
7. **Response Leak Detection** — scans tool output for API key patterns (regex), redacts or blocks

Secrets crypto: AES-256-GCM + HKDF key derivation + system keychain (macOS Keychain / Linux secret-service / Windows DPAPI).

## Plugin System

**WASM Tools** (`src/tools/wasm/`): `.wasm` files in `~/.ironclaw/tools/`. Each declares capabilities (HTTP hosts, secrets). `WasmToolWrapper` implements `Tool` trait. `PROTECTED_TOOL_NAMES` prevents shadowing of built-ins.

**MCP Support** (`src/tools/mcp/`): spawns MCP server processes (stdio), auto-discovers tools from `tools/list`, converts to `Tool` trait.

**Tool Registry** (`src/tools/registry.rs`): `RwLock<HashMap<String, Arc<dyn Tool>>>`. Built-ins protected from shadowing.

## Key Insights for Lyra

1. **LLM Decorator Chain** → #104 (circuit breaker) should be designed as a composable decorator stack, not a single class. Retry + CircuitBreaker + Cache are separate concerns that compose.
2. **Tool Approval Levels** → #106 (plugin system) needs `ApprovalRequirement` from day one. `Never/UnlessAutoApproved/Always` is the right 3-tier model.
3. **Credential Proxy** → #103 (pairing) + #106 (plugins): agents/plugins should never see raw API keys. Inject at the hub boundary.
4. **Hybrid RRF Search** → #83 (memory integration): roxabi-memory's hybrid search should implement RRF fusion, not naive score averaging.
5. **Provider Registry JSON** → Lyra's agent TOML config could adopt the same metadata-driven approach for LLM backends.
6. **NEVER_AUTO_APPROVE_PATTERNS** → blocklist of destructive patterns for shell-level tool approval. Copy this list directly.
