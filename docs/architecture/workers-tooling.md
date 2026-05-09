---
title: Workers, Pipelines & Tooling — Lyra
description: Living reference for runtime workers (CliPool, satellites), tool integration patterns, dev tooling enforcement, and worker observability.
---

# Workers, Pipelines & Tooling — Lyra

> Status: LIVING — current truth for workers, pools, tool registries, dev tooling.
> Last updated: 2026-05-09.
> Source ADRs: 004, 005 (amended), 006, 007, 010, 019, 026, 030, 031, 038, 061, 071.

## Scope

Runtime workers (CliPool subprocess pool, satellite adapters), tool integration patterns (external CLIs, session providers, processor registry), and dev tooling enforcement (importlinter contracts, health monitoring layer boundaries). Excludes adapters (adapters.md), security mechanisms (security-routing.md), and deployment infra (deployment.md).

## Current state

### Workers & lifecycle

#### CliPool cwd resolution

`_LYRA_ROOT` is resolved via a `pyproject.toml` anchor walk (`_find_project_root()`), not a fixed `.parent.parent...` chain. The anchor pattern is robust to file moves and editable installs; it fails loudly at import time if no `pyproject.toml` is found. The fixed parent-chain pattern is prohibited in any path-resolution code that must survive layout changes. `SimpleAgent.__init__` annotates `config` as `Agent` (not `AgentBase.__class__`). → ADR-004

#### CliPool Claude OAuth token

`lyra-clipool.container` injects `CLAUDE_CODE_OAUTH_TOKEN` via `Secret=lyra-claude-oauth,type=env,target=CLAUDE_CODE_OAUTH_TOKEN`. The `claude` CLI has no `--token-file` flag; env is the only delivery path. `ANTHROPIC_API_KEY` is explicitly excluded from `_SAFE_ENV_KEYS` (enforced by unit test) to prevent silent rerouting to Console pay-per-token billing. Token rotation is manual, annual. Re-run the Podman #28075 verification recipe after any Podman version bump on M₁. → ADR-071

#### Wildcard binding & per-scope pools

Phase 1 used a shared `telegram:main:*` pool; all users shared one lock and one CLI subprocess. Issue #125 (Amendment to ADR-005) implemented per-scope pool isolation: `resolve_binding()` now synthesises a concrete pool ID from `Message.extract_scope_id()` (e.g. `telegram:main:chat:555`) when a wildcard binding matches. Each conversation scope gets its own `Pool` with independent history and lock. Rate limiting remains keyed on `msg.user_id` (not the scope) to prevent bypass. CliPool subprocess isolation and memory-namespace isolation per scope remain open (tracked in #112). → ADR-005 (amended)

#### Multi-bot startup

Each active agent gets its own `ProviderRegistry` (with a `SmartRoutingDecorator` configured from that agent's `smart_routing` config) and its own `MessageManager` (keyed to that agent's `i18n_language`). `CliPool` is a single shared instance — it is a process-management resource, not a per-agent config resource. Construction lives in `src/lyra/bootstrap/factory/agent_factory.py::_build_per_agent_registry()`. A startup warning fires when `len(agent_names) > 1` and agents have differing routing configs or languages. → ADR-019

#### Pool callback wiring

`Pool` holds three callbacks (`_session_resume_fn`, `_session_reset_fn`, `_switch_workspace_fn`), all `None` at construction. `AgentBase.configure_pool(pool)` (default no-op; overridden by `SimpleAgent`) wires these callbacks. `MessagePipeline._submit_to_pool` calls `agent.configure_pool(pool)` immediately after `get_or_create_pool()` returns, before `_resolve_context` runs. This eliminates the first-message resume no-op that occurred on daemon restart when `_resolve_context` called `pool.resume_session()` before any `process()` had wired the callback. → ADR-026

#### Hub run-loop error response

When `agent.process()` raises an unexpected exception, the middleware pipeline (currently `pool_processor_exec.py`, relocated from the original `Hub.run()` during ADR-059 hexagonal remediation) sends a user-facing `Response(content="Something went wrong. Please try again.")` instead of silently dropping the message. `dispatch_response()` failures are caught and logged separately. The error string is English; per-locale error responses are deferred. → ADR-006

---

### Tool integration

#### External tool pattern

External CLIs (voicecli, imagecli, gws, scraper) follow a 3-layer Install–Wrap–Declare pattern. **Install**: binary on PATH via `uv tool install` or build script; no fork, no vendor. **Wrap**: a thin `roxabi-plugins` skill (`SKILL.md` only, no code) teaches Claude when and how to invoke the CLI. **Declare**: the agent TOML config lists the tool in `model.tools` (Phase 1: Bash allowlist) or `model.mcp_servers` (Phase 2: MCP). The skill is reusable across any project with the CLI on PATH. Fork into roxabi-plugins only when the upstream is a static asset, abandoned, or requires deep structural changes. → ADR-010

#### Tool-provider protocol

`ScrapeProvider` and `VaultProvider` are async Protocols defined in `lyra.integrations.base`. Concrete implementations (`WebIntelScraper`, `VaultCli`) live in `lyra.integrations.web_intel` and `lyra.integrations.vault_cli`. Both are bundled into a `SessionTools` dataclass injected into every `SessionCommandEntry` as a required (non-optional) parameter. `session_helpers.py` (which previously hardcoded subprocess invocations inside `lyra.core`) is deleted. The `commands/search` plugin receives `VaultProvider` via a module-level injectable set at agent startup, a separate injection path from `SessionCommandEntry`. → ADR-030

#### ProcessorRegistry concurrent dispatch

Slash commands that need conversation history are implemented as `BaseProcessor` subclasses registered via `@register("/cmd")` against a module-level `ProcessorRegistry` singleton. `PoolProcessor._process_one()` calls `pre(msg)` before `agent.process()` and `post(msg, response)` after; responses enter pool history through the normal flow. Self-registration via import in `processors/__init__.py` — a new processor file not listed there is silently invisible. `post()` is only invoked in the non-streaming branch; a streaming agent raises `NotImplementedError` at request time. Outbound delivery uses per-scope `asyncio.Lock` fan-out: tasks for different scopes run concurrently; tasks within the same scope are ordered. Idle locks are reaped when `_scope_locks` exceeds 256 entries. → ADR-031

---

### Tooling & invariants

#### Importlinter port-import fix

The `shared-modules-independence` contract enforces peer isolation between 8 floating modules (`lyra.obs`, `lyra.stt`, `lyra.tts`, `lyra.errors`, `lyra.config`, `lyra.integrations`, `lyra.monitoring`, `lyra.agent_cmd`). As of 2026-05-08, 4 `ignore_imports` suppressions remain. The target is 2: fix `lyra.core.agent.agent` to import `STTProtocol`/`TtsProtocol` from `lyra.core.ports.*` (not from `lyra.stt`/`lyra.tts`), and introduce `SessionToolsProtocol` in `lyra.core.ports.integrations` so `processor_registry.py` no longer imports the concrete `SessionTools` from `lyra.integrations.base`. The two remaining suppressions (`core/ports/stt.py → lyra.stt:TranscriptionResult` and `core/ports/tts.py → lyra.tts:SynthesisResult`) are TYPE_CHECKING-only and track a separate result-type migration. → ADR-061

#### Health monitoring layer boundaries

Three observation layers: (1) in-process self-monitoring (circuit breakers, error counts), (2) `/health/detail` endpoint (aggregated snapshot), (3) external monitor (process alive, endpoint reachable, OS resources). Each layer observes only what it can see directly. `check_dead_backend` and the `dead_backend_hits` counter are removed — the circuit breaker (`check_circuits`) is the correct and accurate signal for LLM backend health. Silent CLI protocol failures (empty stdout, NDJSON parse error, unexpected subprocess exit) must be classified as `CliResult(ok=False)` in `cli_protocol.py` to route to `cb.record_failure()`. Active monitor checks: `check_process`, `check_http_health`, `check_queue_depth`, `check_idle`, `check_circuits`, `check_reaper`, `check_disk`. → ADR-038

#### Model-config mismatch

When `CliPool.send()` receives a `ModelConfig` that differs from the one used to spawn the existing `_ProcessEntry`: the **streaming path** (`cli_pool_streaming.py`) respawns the subprocess (Phase 2 behaviour). The **non-streaming path** (`cli_pool.py`) still logs a warning and silently ignores the new config (Phase 1 behaviour). Full migration of the non-streaming path is gated on the model-selector SLM introduction. Session continuity across model changes is an open architectural decision for that phase. → ADR-007

---

## Key invariants

- `_find_project_root()` anchor walk is the only permitted pattern for locating `_LYRA_ROOT`; fixed `.parent` chains are prohibited.
- `ANTHROPIC_API_KEY` must never appear in `_SAFE_ENV_KEYS`; the unit test `test_anthropic_api_key_not_forwarded` is load-bearing.
- `configure_pool(pool)` must be called before `_resolve_context` on every message; lazy callback wiring inside `process()` is insufficient.
- Each agent gets its own `ProviderRegistry` and `MessageManager`; `CliPool` is the only shared process resource.
- Every `SessionCommandEntry` holds a fully-constructed `SessionTools`; the `tools` parameter is required, not optional.
- `processors/__init__.py` must import every processor module; a missing import silently gaps the registry.
- `BaseProcessor.post()` is only invoked in the non-streaming branch; processors are incompatible with streaming agents.
- Health checks are layer-bound: external monitor checks process/endpoint/OS only; circuit breakers own LLM backend health.
- Per-scope pool isolation is active at the Hub layer; CliPool subprocess isolation and memory-namespace isolation per scope remain open.
- `type=env` is a documented exception for `CLAUDE_CODE_OAUTH_TOKEN`; all other secrets use `type=mount`. Re-verify after Podman upgrades.

---

## Open questions / known gaps

- ADR-061: 4 `ignore_imports` remain — target is 2; `lyra.core.ports.integrations` (`SessionToolsProtocol`) not yet created; import sites in `agent.py` and `processor_registry.py` not yet updated.
- ADR-007: non-streaming path (`cli_pool.py`) still silently ignores model-config mismatch; migration gated on model-selector SLM.
- ADR-005 / #112: CliPool subprocess isolation (one subprocess per scope) and memory-namespace isolation per scope are not yet implemented; only Hub-layer pool isolation is complete.
- ADR-038: audit of `cli_protocol.py` for silent-failure paths not confirmed complete; some empty-stdout failures may not yet reach `cb.record_failure()`.
- ADR-031: `register_session_command` (legacy) and `@register` (ProcessorRegistry) coexist without a migration deadline; new commands should use `@register`.

---

## See also

- Adapters → `adapters.md`
- Deployment & Quadlet → `deployment.md`
- Security & credentials → `security-routing.md` (ADR-071 cross-listed)
- LLM provider layer → `architecture-patterns.md`

---

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 004 | CliPool cwd resolution & AgentBase annotation | Accepted |
| 005 | Wildcard binding & per-scope pools | Amended (#125) |
| 006 | Hub run-loop error reply | Accepted |
| 007 | Model-config mismatch | Accepted (non-streaming: known limitation) |
| 010 | External tool pattern | Accepted |
| 019 | Multi-bot startup resource sharing | Accepted |
| 026 | Pool callback wiring — eager at pool resolution | Accepted |
| 030 | Tool-provider protocol for session commands | Accepted |
| 031 | ProcessorRegistry & concurrent outbound dispatch | Accepted |
| 038 | Health monitoring layer boundaries | Accepted |
| 061 | Importlinter port-import fix | Accepted (partially implemented) |
| 071 | CliPool Claude OAuth token mechanism | Accepted |
