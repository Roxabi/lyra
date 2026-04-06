# Extension Points Guide — Lyra — 2026-04-06

## Overview

Lyra follows a hub-and-spoke architecture where the Hub is the sole message router and all external-facing components plug in through typed Protocols or structural interfaces. The system relies on Python's `typing.Protocol` with `runtime_checkable` for its key pluggability contracts, a dict-based `ProviderRegistry` for LLM backends, and a composable `MiddlewarePipeline` for inbound message processing. Extension happens by implementing the right Protocol, instantiating the concrete class, and registering it at bootstrap time — no base-class inheritance required except for `AgentBase`.

---

## Extension Point Matrix

| Component | Protocol / Interface | File : line | Bootstrap injection point | Complexity |
|---|---|---|---|---|
| LLM provider | `LlmProvider` (Protocol) | `src/lyra/llm/base.py:33` | `bootstrap/agent_factory.py:_build_shared_base_providers()` + `ProviderRegistry.register()` | Low |
| Channel adapter | `ChannelAdapter` (Protocol) | `src/lyra/core/hub/hub_protocol.py:24` | `bootstrap/bootstrap_wiring.py:wire_telegram_adapters()` / `wire_discord_adapters()` | High |
| Memory backend | `MemoryManager` (concrete class, no Protocol) | `src/lyra/core/memory.py:38` | `bootstrap/hub_standalone.py` — injected as `Hub(stt=..., tts=...)` | High |
| STT engine | `STTProtocol` (Protocol) | `src/lyra/stt/__init__.py:19` | `bootstrap/voice_overlay.py:init_nats_stt()` → `Hub(stt=...)` | Medium |
| TTS engine | `TtsProtocol` (Protocol) | `src/lyra/tts/__init__.py:23` | `bootstrap/voice_overlay.py:init_nats_tts()` → `Hub(tts=...)` | Medium |
| Pipeline middleware | `PipelineMiddleware` (Protocol) | `src/lyra/core/hub/middleware.py:77` | `src/lyra/core/hub/middleware.py:build_default_pipeline()` | Low |
| Slash command plugin | `plugin.toml` + `handlers.py` | `src/lyra/commands/echo/` | `CommandLoader` discovers from `commands/` dir at agent init | Low |
| Agent personality | TOML schema / `AgentRow` | `src/lyra/core/agent_seeder.py` / `src/lyra/core/agent_models.py:26` | `lyra agent init` seeds DB; loaded at hub startup | Low |

---

## Detailed Guides

### Adding a new LLM provider

**Protocol:** `src/lyra/llm/base.py:33` — `LlmProvider`

```python
class LlmProvider(Protocol):
    capabilities: dict[str, Any]

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> LlmResult: ...

    def is_alive(self, pool_id: str) -> bool: ...

    # Optional — duck-typed, checked via hasattr()
    async def stream(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]: ...
```

**Steps:**

1. Create `src/lyra/llm/drivers/mydriver.py`. Implement `LlmProvider` structurally (no import of the Protocol needed). Set `capabilities = {"streaming": False, "auth": "..."}`.
2. `complete()` must return `LlmResult`. Set `retryable=False` for non-transient errors (quota, bad key).
3. Implement `stream()` only if the backend supports real-time token delivery. It is duck-typed — callers check `hasattr(provider, "stream")`.
4. Register the driver in `bootstrap/agent_factory.py:_build_shared_base_providers()`:
   ```python
   from lyra.llm.drivers.mydriver import MyDriver
   providers["my-backend"] = MyDriver(...)
   ```
5. Add `"my-backend"` to `_VALID_BACKENDS` in `src/lyra/core/agent_config.py:11`.
6. Add a branch to `bootstrap/agent_factory.py:_create_agent()` so agents with `backend = "my-backend"` are wired to the correct agent class.
7. Set `backend = "my-backend"` and `model = "..."` in the agent TOML / DB.

**Decorator stack** (optional but recommended for production): wrap your driver in `RetryDecorator` → `CircuitBreakerDecorator` from `src/lyra/llm/decorators.py`. The existing `_build_shared_base_providers()` shows the pattern.

---

### Adding a new communication channel (platform adapter)

**Protocol:** `src/lyra/core/hub/hub_protocol.py:24` — `ChannelAdapter`

Key methods:

| Method | Purpose |
|---|---|
| `normalize(raw)` | Translate platform event → `InboundMessage` |
| `normalize_audio(raw, audio_bytes, mime_type, *, trust_level)` | Translate audio event → `InboundAudio` |
| `send(original_msg, outbound)` | Send a complete text response |
| `send_streaming(original_msg, events, outbound)` | Stream edit-in-place response |
| `render_audio(msg, inbound)` | Send a voice note |
| `render_audio_stream(chunks, inbound)` | Stream audio chunks |
| `render_voice_stream(chunks, inbound)` | Play TTS into an active voice session |
| `render_attachment(msg, inbound)` | Send a file/image |

**Steps:**

1. Create `src/lyra/adapters/whatsapp.py` (or similar). Implement all methods of `ChannelAdapter` structurally — no inheritance. Use `src/lyra/adapters/telegram.py` as the reference.
2. Security contract: verify sender identity at the platform level before constructing `InboundMessage`. Never derive `user_id` or `scope_id` from unverified data.
3. Add config model classes to `src/lyra/config.py` (e.g. `WhatsAppBotConfig`, `WhatsAppMultiConfig`).
4. Add a `wire_whatsapp_adapters()` function in `src/lyra/bootstrap/bootstrap_wiring.py` mirroring `wire_telegram_adapters()`. It must call:
   - `hub.register_authenticator(Platform.WHATSAPP, bot_id, auth)`
   - `hub.register_adapter(Platform.WHATSAPP, bot_id, adapter)`
   - `hub.register_binding(Platform.WHATSAPP, bot_id, "*", agent_name, key.to_pool_id())`
   - `hub.register_outbound_dispatcher(Platform.WHATSAPP, bot_id, dispatcher)`
5. Add `WHATSAPP = "whatsapp"` to `Platform` enum in `src/lyra/core/message.py`.
6. Call `wire_whatsapp_adapters()` from `bootstrap/hub_standalone.py` (standalone mode) and `bootstrap/unified.py` (single-process mode).
7. Add `[[whatsapp.bots]]` config schema to `config.toml` docs and the config loader.

**Three-process mode (production):** the adapter runs in its own supervisor process. It publishes inbound messages to `lyra.inbound.<platform>.<bot_id>` NATS subject. The hub subscribes via `NatsBus`. Outbound responses arrive via `lyra.outbound.<platform>.<bot_id>`. See `src/lyra/nats/nats_channel_proxy.py` for how the existing NATS proxy implements `ChannelAdapter`.

---

### Adding a new memory backend

**Current state:** `MemoryManager` at `src/lyra/core/memory.py:38` is a concrete class wrapping `roxabi_vault.AsyncMemoryDB`. There is no `MemoryProtocol` — this is a gap (see "What's Missing" below).

**Steps (current approach — subclass):**

1. Subclass `MemoryManager` or write a duck-typed replacement that exposes the methods actually called by callers:
   - `connect() -> None`
   - `close() -> None`
   - `recall(user_id, namespace, first_msg, token_budget) -> str`
   - `set_alias_store(store)`
   - `get_identity_anchor(namespace) -> str | None`
   - Upsert methods from `MemoryManagerUpserts` (see `src/lyra/core/memory_upserts.py`)
2. Inject your implementation at bootstrap. In `bootstrap/bootstrap_stores.py`, the `open_stores()` context manager constructs the stores including `MemoryManager`. Replace the construction there.
3. The `MemoryManager` instance is passed to `AgentBase` via the agent factory chain. If you inject a duck-typed replacement, confirm all callers that rely on `isinstance(mem, MemoryManager)` checks (none currently, but verify before shipping).

---

### Adding a new STT engine

**Protocol:** `src/lyra/stt/__init__.py:19` — `STTProtocol`

```python
@runtime_checkable
class STTProtocol(Protocol):
    async def transcribe(self, path: Path | str) -> TranscriptionResult: ...
```

`TranscriptionResult` (dataclass at line 32): `text: str`, `language: str`, `duration_seconds: float`.

**Three-process mode (production path):** The hub never calls `STTService` directly — it talks to the voiceCLI STT adapter over NATS via `NatsSttClient` (`src/lyra/nats/nats_stt_client.py`). `NatsSttClient` implements `STTProtocol`.

**Steps for a new in-process STT engine:**

1. Create a class with `async def transcribe(self, path: Path | str) -> TranscriptionResult`. The `@runtime_checkable` decorator means `isinstance(obj, STTProtocol)` works without explicit inheritance.
2. In `bootstrap/voice_overlay.py:init_nats_stt()`, return your engine instead of `NatsSttClient`. Or add a new env-var branch to select between implementations.
3. The result is passed as `Hub(stt=your_engine)` and then forwarded to each `_create_agent()` call.

**Steps for a new NATS-based STT microservice:**

1. Implement a service that listens on `lyra.voice.stt.request` and replies with `{"ok": true, "text": "...", "language": "...", "duration_seconds": 0.0}`. See the request schema in `NatsSttClient.transcribe()`.
2. No Lyra code changes needed — the existing `NatsSttClient` already handles the protocol.

---

### Adding a new TTS engine

**Protocol:** `src/lyra/tts/__init__.py:23` — `TtsProtocol`

```python
@runtime_checkable
class TtsProtocol(Protocol):
    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: AgentTTSConfig | None = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult: ...
```

`SynthesisResult` (dataclass at line 43): `audio_bytes: bytes`, `mime_type: str`, `duration_ms: int | None`, `waveform_b64: str | None`.

**Three-process mode (production path):** The hub calls `NatsTtsClient` (`src/lyra/nats/nats_tts_client.py`) which implements `TtsProtocol`. Replace or supplement by implementing a service that listens on `lyra.voice.tts.request`.

**Steps for a new in-process TTS engine:**

1. Create a class with `async def synthesize(self, text, *, agent_tts, language, voice, fallback_language) -> SynthesisResult`. Return OGG/Opus audio with `mime_type="audio/ogg"`.
2. In `bootstrap/voice_overlay.py:init_nats_tts()`, return your engine.
3. Passed as `Hub(tts=your_engine)` and forwarded to agents.

**Steps for a new NATS-based TTS microservice:**

1. Listen on `lyra.voice.tts.request`. Reply with `{"ok": true, "audio_b64": "<base64>", "mime_type": "audio/ogg", "duration_ms": N, "waveform_b64": "<base64>"}`. See `NatsTtsClient.synthesize()` for the full request schema (fields: `text`, `language`, `voice`, `fallback_language`, `chunked`, `engine`, `accent`, `personality`, `speed`, `emotion`, `exaggeration`, `cfg_weight`, `segment_gap`, `crossfade`, `chunk_size`).
2. No Lyra code changes needed.

---

### Adding pipeline middleware

**Protocol:** `src/lyra/core/hub/middleware.py:77` — `PipelineMiddleware`

```python
class PipelineMiddleware(Protocol):
    async def __call__(
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult: ...
```

`Next = Callable[[InboundMessage, PipelineContext], Awaitable[PipelineResult]]`

- Return `await next(msg, ctx)` to pass through to the next stage.
- Return `_DROP` (from `message_pipeline.py`) or any `PipelineResult` to short-circuit.
- `PipelineContext` carries `hub`, `key`, `binding`, `agent`, `pool`, `router`, `trace_hook`, `event_bus`.

**Default pipeline order** (10 stages, `build_default_pipeline()` at line 161):

```
TraceMiddleware → ValidatePlatformMiddleware → ResolveTrustMiddleware → TrustGuardMiddleware
→ RateLimitMiddleware → SttMiddleware → ResolveBindingMiddleware → CreatePoolMiddleware
→ CommandMiddleware → SubmitToPoolMiddleware
```

**Steps:**

1. Create a class with `async def __call__(self, msg, ctx, next)`. No inheritance needed.
2. Open `src/lyra/core/hub/middleware.py:build_default_pipeline()`.
3. Import your class and insert it into the `MiddlewarePipeline([...])` list at the correct position. Stages run sequentially in list order.
4. Inject any dependencies via `__init__` — the pipeline list is constructed at bootstrap so DI is straightforward.

---

### Adding a slash command plugin

**Structure:** each plugin is a subdirectory under `src/lyra/commands/` with:

- `plugin.toml` — manifest
- `handlers.py` — async handler functions

**`plugin.toml` schema:**

```toml
name = "myplugin"
description = "What it does"
version = "0.1.0"
priority = 100        # lower = higher priority in load order
enabled = true
timeout = 30.0        # per-handler timeout (seconds)

[[commands]]
name = "mycommand"    # /mycommand in chat
description = "What this command does"
handler = "cmd_mycommand"   # function name in handlers.py
```

**Handler signature:**

```python
async def cmd_mycommand(
    msg: InboundMessage,
    pool: Pool,
    args: list[str],
) -> Response:
    return Response(content="...")
```

**Steps:**

1. Create `src/lyra/commands/myplugin/plugin.toml` and `handlers.py`.
2. Enable the plugin per-agent in the agent DB/TOML: `plugins.enabled = ["myplugin"]`.
3. `CommandLoader` discovers plugins from the `commands/` directory at agent initialization — no registration call needed.
4. Command names must not conflict with built-ins (`help`, `stop`, `circuit`, `routing`, `config`, `clear`, `new`, `workspace`, `folder`, `vault-add`, `explain`, `summarize`, `search`).

---

### Defining an agent personality (TOML schema)

Agent profiles are seeded from TOML files (two locations, in precedence order):
1. `~/.lyra/agents/<name>.toml` — user-level overrides (machine-specific, gitignored)
2. `src/lyra/agents/<name>.toml` — bundled system defaults

Import into DB with `lyra agent init` (or `--force` to overwrite). Startup reads only from DB.

**Complete TOML schema** (all fields, parsed by `src/lyra/core/agent_seeder.py`):

```toml
[agent]
name = "myagent"                  # required, alphanumeric + _ -
system_prompt = "You are..."      # required
memory_namespace = "myagent"      # used for recall isolation
show_intermediate = false         # show ⏳ intermediate turns to user
show_tool_recap = true            # show 🔧 tool summary cards
permissions = []                  # extra permission strings (future use)
passthroughs = []                 # slash commands forwarded straight to LLM

[model]
backend = "anthropic-sdk"         # "anthropic-sdk" | "claude-cli" | "ollama"
model = "claude-3-5-haiku-20241022"
max_turns = 10                    # 0 or absent = unlimited
tools = []                        # allowed tools (empty = backend defaults)
cwd = "/path/to/project"          # working dir for claude-cli subprocess
skip_permissions = false
streaming = false                 # enable real-time streaming (claude-cli only)

[agent.smart_routing]             # only for anthropic-sdk
enabled = false
history_size = 50
routing_table = { trivial = "claude-haiku-...", complex = "claude-opus-..." }
high_complexity_commands = []

[plugins]
enabled = ["echo", "search"]      # plugin names to activate for this agent

[tts]                             # per-agent TTS defaults (all optional)
engine = "qwen"
voice = "Aria"
language = "en"
accent = null
personality = null
speed = null
emotion = null
exaggeration = null
cfg_weight = null
segment_gap = null
crossfade = null
chunk_size = null
languages = ["fr", "en"]         # detection candidates
default_language = "en"          # fallback when detection fails

[stt]                             # per-agent STT params (all optional)
language_detection_threshold = null
language_detection_segments = null
language_fallback = "en"

[workspaces]
myproject = "/home/user/projects/myproject"  # accessible via /workspace myproject

[commands.mycommand]              # per-command config (future use)
enabled = true

[i18n]
default_language = "en"           # fallback language for TTS + messages

[patterns]                        # rewrite rules (#345)
strip_markdown = true

[passthroughs]                    # top-level list alt to agent.passthroughs
# ["voice", "search"]
```

---

## What's Missing (Gaps in Pluggability)

| Gap | Impact | Location |
|---|---|---|
| `MemoryManager` has no Protocol | Swapping the memory backend requires subclassing or careful duck-typing; no formal contract | `src/lyra/core/memory.py` |
| `AgentBase` is an ABC, not a Protocol | New agent implementations must inherit `AgentBase` (pulls in `SessionManager`, `CommandLoader`, etc.) | `src/lyra/core/agent.py:38` |
| LLM backend selection is if/elif in `_create_agent()` | Adding a new backend requires editing `agent_factory.py` rather than a registry entry | `src/lyra/bootstrap/agent_factory.py:151-206` |
| `ChannelAdapter` has no NATS adapter base | Writing a new platform for three-process mode requires re-implementing the full NATS publish/subscribe pattern from scratch | `src/lyra/nats/nats_channel_proxy.py` |
| `Platform` enum must be extended manually | New channels require a code change in `src/lyra/core/message.py` (not config-driven) | `src/lyra/core/message.py` |
| No plugin discovery outside `src/lyra/commands/` | Third-party command plugins cannot be dropped in without a source code change to the plugin path | `src/lyra/core/agent.py:35` (`_COMMANDS_DIR`) |

---

## Recommended Improvements

| Improvement | Benefit | Effort |
|---|---|---|
| Extract `MemoryProtocol` from `MemoryManager` (same pattern as `LlmProvider`) | Enables swapping memory backends without subclassing | Low |
| Replace if/elif in `_create_agent()` with registry lookup (`agent_factory_registry: dict[str, Callable]`) | New LLM backends register themselves; no editing of agent_factory.py | Low |
| Promote `_COMMANDS_DIR` to a config-driven list (e.g. `LYRA_PLUGINS_PATH` env var) | Third-party plugins installable without source changes | Medium |
| Extract `NatsPlatformAdapterBase` for three-process channel adapters | Reduces boilerplate when adding a new platform (WhatsApp, Slack) | Medium |
| Make `Platform` an open string registry rather than a closed enum | New platforms without code change in `message.py` | Medium — breaks existing isinstance checks |
| Convert `AgentBase` ABC to a Protocol for agent implementations | Allows fully independent agent implementations; reduces coupling to `SessionManager` | High — large refactor |
