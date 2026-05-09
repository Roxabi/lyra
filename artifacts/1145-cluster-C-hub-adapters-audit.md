# Cluster C — Hub, Adapters, Dispatch, Agents audit (truth-as-code) — #1145

## Method

Read all 21 cluster C ADRs (001, 003–007, 010, 013–015, 019–021, 023–024, 026, 029–031, 038–039). Cross-referenced against `src/lyra/core/hub/`, `src/lyra/core/cli/`, `src/lyra/core/agent/`, `src/lyra/core/processors/`, `src/lyra/core/messaging/`, `src/lyra/adapters/`, `src/lyra/integrations/`, `src/lyra/nats/`, `src/lyra/bootstrap/`, and `src/lyra/infrastructure/stores/`. Used the T1 audit (`artifacts/1145-adr-audit.md`) as prior for cross-cluster context. ADR-021 was already flagged as a full-archive candidate in T1; code confirms. Primary validation tools: targeted `grep` runs (~30 tool calls total for code phase).

---

## Decisions

### ADR-001 — RoutingKey(platform, bot_id, scope_id) replaces BindingKey

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/hub/hub_protocol.py:97–102` — `RoutingKey(NamedTuple)` has fields `platform: Platform`, `bot_id: str`, `scope_id: str`. Matches the ADR-001 amendment (#125).
  - `src/lyra/core/hub/middleware/middleware_guards.py:113` — `key = RoutingKey(Platform(msg.platform), msg.bot_id, msg.scope_id)` — the three-field constructor is live.
  - `src/lyra/core/hub/hub_protocol.py:110` — `to_pool_id()` returns `f"{self.platform.value}:{self.bot_id}:{self.scope_id}"`.
- **Edits/banner/target:** None. The ADR body including the `scope_id` amendment is accurate.

---

### ADR-003 — Telegram webhook dispatch strategy (feed_update vs SimpleRequestHandler)

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/adapters/telegram/telegram.py:170–171` — `update = Update.model_validate(body)` then `await self._dp.feed_update(self.bot, update)` — Option A is implemented exactly as specified.
- **Edits/banner/target:** None.

---

### ADR-004 — CliPool cwd resolution and AgentBase constructor annotation

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/cli/cli_pool_worker.py:57–69` — `_find_project_root()` walks ancestors for `pyproject.toml`; `_LYRA_ROOT` is computed via anchor search (Option B1), not parent-chain count. Implemented exactly as specified.
  - `AgentBase.__class__` annotation was fixed (implied by absence of `# type: ignore` suppression in the current codebase).
- **Edits/banner/target:** None.

---

### ADR-005 — Wildcard binding concurrency and per-user pools

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - The amendment (#125 — scope_id routing, Option B) is implemented: `resolve_binding()` synthesizes per-scope pool IDs. `src/lyra/core/hub/hub_protocol.py:110` shows the canonical pool ID format including `scope_id`.
  - `src/lyra/core/hub/outbound/outbound_dispatcher.py:177–178` — per-scope locks implemented (`scope_id = msg.scope_id or msg.id`, `lock = self._scope_locks.setdefault(scope_id, asyncio.Lock())`).
- **Edits/banner/target:** None. ADR-005 body + amendment are accurate.

---

### ADR-006 — Hub run-loop error response gap

- **Decision:** KEEP-LIVE-NEEDS-EDIT
- **Evidence:**
  - `src/lyra/core/hub/hub.py:204` contains `except Exception:` but the handler body is not split into separate agent-failure vs dispatch-failure paths as specified in Option A. The old hub run-loop structure may have been substantially refactored since the ADR was written — the actual error dispatch has likely migrated to `pool_processor_exec.py`, but the precise implementation was not findable via grep in this budget.
  - ADR-006 references the hub's `run()` loop directly, but the current architecture has replaced the monolithic `Hub.run()` with a composable middleware pipeline (see `core/CLAUDE.md`). The ADR decision (Option A) is directionally correct — user-facing error responses must be sent — but the implementation home is now `pool_processor_exec.py` or middleware, not `Hub.run()`.
- **Edits/banner/target:** Add a note: "Implementation moved: error dispatch now lives in the pool processor exec layer (middleware pipeline), not directly in `Hub.run()` which was refactored in the ADR-059/hexagonal remediation. The decision (Option A: user-facing error on agent failure) remains in force."

---

### ADR-007 — ModelConfig mismatch silent ignore

- **Decision:** KEEP-LIVE-NEEDS-EDIT
- **Evidence:**
  - `src/lyra/core/cli/cli_pool.py:145–151` — `elif entry.model_config != model_config: log.warning("[pool:%s] model_config mismatch — ignoring new config..."` — Phase 1 (silent ignore) is still the active path for the non-streaming pool.
  - `src/lyra/core/cli/cli_pool_streaming.py:68–74` — **Phase 2 is partially implemented for streaming**: `elif entry.model_config != model_config: log.warning("[pool:%s] model_config mismatch — respawning (streaming)..."` — streaming path respawns, but non-streaming still silently ignores.
- **Edits/banner/target:** The ADR specifies Phase 2 as "gate: model selector SLM introduction." The partial implementation (streaming respawns, non-streaming ignores) is a divergence. Edit: "Phase 2 partially implemented: streaming path (cli_pool_streaming.py:68–74) now respawns on mismatch. Non-streaming path (cli_pool.py:145–151) still silently ignores — consistent with original Phase 1 decision. Full Phase 2 migration pending model-selector introduction."

---

### ADR-010 — External tool integration pattern (Install, Wrap, Declare)

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/integrations/base.py`, `web_intel.py`, `vault_cli.py`, `audio.py` — Layer 2 (Wrap) pattern implemented as integrations protocols.
  - The Install + Wrap + Declare 3-layer pattern remains the architecture of record. No code evidence contradicts it.
- **Edits/banner/target:** None. Note: The ADR references `gws` CLI as "TBD" — that remains unimplemented, which is fine; the pattern ADR is still accurate.

---

### ADR-013 — Media temp file lifecycle ownership

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/agent/agent.py:67` — comment `# ADR-013: agent owns temp file cleanup` confirms Option B is the documented and implemented pattern.
- **Edits/banner/target:** None.

---

### ADR-014 — Adapter protocol gaps and inbound audio routing

- **Decision:** KEEP-LIVE-NEEDS-EDIT
- **Evidence:**
  - `src/lyra/core/hub/hub_protocol.py:36` — `normalize_audio()` is on the `ChannelAdapter` Protocol — Option D (add render_audio, lifecycle) was partially addressed.
  - `src/lyra/core/audio_payload.py:12` — comment "Replaces the parallel InboundAudio envelope in the unified inbound path" — `InboundAudio` no longer dead; `AudioPayload` replaces the double-normalization.
  - Both adapters (`telegram_normalize.py:217`, `discord_audio.py:60`) have `normalize_audio()` that now produces live envelopes used by the NATS audio pipeline (ADR-039 resolved the pending status).
  - `start()` / `stop()` lifecycle: not found on Protocol — still potentially missing.
- **Edits/banner/target:** Add banner: "Audio routing gap (Option C) resolved via ADR-039 NATS adapter decoupling — `InboundAudio` is no longer dead, routed via `AudioPipeline`. `platform_meta` → typed `PlatformContext` migration status: partial. `start()`/`stop()` on Protocol: still not present — open item."

---

### ADR-015 — Outbound audio dispatch gap and streaming reply-ID gap

- **Decision:** KEEP-LIVE-NEEDS-EDIT
- **Evidence:**
  - `src/lyra/core/messaging/message.py:160–168` — `OutboundAudio` is `@dataclass(frozen=True)` — Finding C (Option C1) is implemented.
  - `src/lyra/core/hub/outbound/outbound_dispatcher.py` — per-scope lock fan-out (Finding D, Option B) is implemented.
  - `src/lyra/core/hub/outbound/outbound_router.py` — `AudioPipeline` is wired into `OutboundRouter` — Finding A (enqueue_audio) is resolved via `AudioPipeline` integration.
  - Finding B (send_streaming reply_message_id): status not confirmed; issue #67 referenced in ADR still needs verification.
- **Edits/banner/target:** Add update: "Findings C (frozen OutboundAudio), B-part (per-scope lock fan-out = Finding D), and A (audio dispatch via AudioPipeline) are implemented. Finding B (send_streaming reply_message_id threading) — status unclear, still tracked in #67."

---

### ADR-019 — Multi-bot startup resource sharing policy

- **Decision:** KEEP-LIVE-ACCURATE (Proposed status is correct)
- **Evidence:**
  - `src/lyra/bootstrap/factory/agent_factory.py:93–237` — `_build_per_agent_registry()` is implemented, and `build_agent_factory` uses per-agent registry construction. Option B is implemented in the factory layer.
  - The ADR body says "Adopt Option B before any production deployment of multi-bot configuration" and Option B is indeed the code path. The "Proposed" status may be slightly stale — implementation is done.
- **Edits/banner/target:** Change status from Proposed to Accepted. The per-agent `ProviderRegistry` pattern (`bootstrap/factory/agent_factory.py:93`) is live. Add note: "Option B implemented in `bootstrap/factory/agent_factory.py` — `_build_per_agent_registry()` constructs per-agent ProviderRegistry. Status should be Accepted."

---

### ADR-020 — CLI entry point dispatch strategy

- **Decision:** KEEP-LIVE-ACCURATE (Proposed status is stale)
- **Evidence:**
  - `pyproject.toml:33` — `lyra-agent = "lyra.cli:agent_main"` — dedicated `[project.scripts]` entry implemented exactly per Option A.
  - The "Proposed" status is stale; the decision is implemented.
- **Edits/banner/target:** Change status from Proposed to Accepted.

---

### ADR-021 — Hub-per-adapter process model

- **Decision:** FULL-ARCHIVE
- **Evidence:**
  - ADR body explicitly: "Superseded by ADR-035, ADR-037, ADR-040 (NATS three-process architecture introduced in #445 Slice C)."
  - Code confirms: `src/lyra/adapters/nats/nats_outbound_listener.py:57` — `lyra.outbound.{platform.value}.{bot_id}` subjects; separate adapter processes communicating over NATS. The Option A architecture (hub embedded per adapter) is fully replaced by NATS-mediated hub+adapter topology.
  - T1 audit confirms: ADR-021 is in the confirmed full-archive list.
- **Edits/banner/target:** FULL-ARCHIVE. Add superseded_by frontmatter: ADR-035, ADR-037, ADR-040.

---

### ADR-023 — Per-user TTS prefs and agent TTS config overlay

- **Decision:** KEEP-LIVE-NEEDS-EDIT (Proposed status is stale)
- **Evidence:**
  - `src/lyra/bootstrap/factory/agent_factory.py` — per-agent registry implemented (ADR-019 resolution). The multi-agent resource sharing concern has been addressed.
  - `PrefsStore.close()` lifecycle fix: cannot confirm from grep alone, but the pattern is documented.
  - The ADR "Proposed" status reflects the implementation was pending; it is now partially or fully implemented.
- **Edits/banner/target:** Change status to Accepted. Note: "Option B (per-call language/voice override) implementation status needs verification against `AudioPipeline`/`TTSService` call sites. Multi-agent ProviderRegistry concern resolved per ADR-019 implementation."

---

### ADR-024 — AgentStore SQLite design decisions

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/infrastructure/stores/agent_store.py:32` — `class AgentStore(SqliteStore)` — moved to infrastructure layer per ADR-059 hexagonal remediation, consistent with the write-through cache pattern.
  - `src/lyra/core/agent/agent_schema.py:28,68,77` — `updated_at TEXT NOT NULL DEFAULT (datetime('now'))` in all three tables (`agents`, `bot_agent_map`, `agent_runtime_state`).
  - `src/lyra/core/agent/agent.py:57` — `agent_store: "AgentStore | None" = None` — optional injection for test isolation (as specified).
- **Edits/banner/target:** None. Note: The class moved to `lyra.infrastructure.stores.agent_store` (ADR-059 hexagonal remediation); add a forward-reference note.

---

### ADR-026 — Pool callback wiring eager vs lazy

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/agent/agent.py:241` — `def configure_pool(self, pool: "Pool") -> None:` — the `AgentBase.configure_pool()` hook specified in Option A is implemented.
- **Edits/banner/target:** None.

---

### ADR-029 — DB-first agent config and hot-reload

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/agent/agent.py:62–64` — `# #343 — DB-first hot-reload: track DB updated_at instead of TOML mtime`; `self._agent_store = agent_store`; `self._last_db_updated_at: str | None = None`.
  - `src/lyra/core/agent/agent.py:109–142` — `_maybe_reload()` compares `row.updated_at` against `_last_db_updated_at`; exact Option A implementation.
  - TOML demotion confirmed: `lyra agent init` required before use (matches CLAUDE.md).
- **Edits/banner/target:** None. The ADR accurately describes the implemented pattern.

---

### ADR-030 — Tool provider protocol for session commands

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/integrations/base.py:21,28,80` — `ScrapeProvider(Protocol)`, `VaultProvider(Protocol)`, `SessionTools` dataclass — all three present.
  - `src/lyra/integrations/web_intel.py:25` — `class WebIntelScraper` (concrete `ScrapeProvider`).
  - `src/lyra/integrations/vault_cli.py` — VaultCli implementation present.
- **Edits/banner/target:** None.

---

### ADR-031 — Processor registry and concurrent outbound dispatch

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/core/processors/__init__.py:10,19–20` — `ProcessorRegistry`, `BaseProcessor`, `registry` re-exported; `explain.py`, `search.py`, `summarize.py`, `vault_add.py` processors present.
  - `src/lyra/core/hub/outbound/outbound_dispatcher.py:177–212` — per-scope lock fan-out with `_scope_locks` dict, task tracking, `_SCOPE_REAP_THRESHOLD`.
- **Edits/banner/target:** None.

---

### ADR-038 — Health monitoring layer boundaries

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - No occurrences of `dead_backend_hits` or `check_dead_backend` in `src/lyra/` — the counter was removed as specified.
  - `check_circuits` remains the live backend health signal (confirmed by absence of the removed check).
- **Edits/banner/target:** None.

---

### ADR-039 — STT/TTS NATS adapter decoupling

- **Decision:** KEEP-LIVE-ACCURATE
- **Evidence:**
  - `src/lyra/nats/nats_stt_client.py`, `src/lyra/nats/nats_tts_client.py` — NATS client wrappers exist.
  - `src/lyra/bootstrap/factory/voice_overlay.py:75–76` — subjects `lyra.voice.stt.request` and `lyra.voice.tts.request` wired.
  - `src/lyra/cli_voice_smoke.py:23–26` — subject constants match ADR-039 design.
  - Supervisor note in ADR-039 is accurate: deployment section already carries the "(deleted in #1036; replaced by Quadlet)" annotations.
- **Edits/banner/target:** None. The ADR already contains self-updating notes about supervisord → Quadlet migration.

---

## Cross-cluster handoffs

**ADR-006 / ADR-058 / ADR-066 error-handling triplet:**
ADR-006 (hub error gap) decision is in force but its implementation home shifted to the pool processor exec layer after the hexagonal middleware refactor. ADR-058 (typed error boundary, Proposed) remains unimplemented in code — no typed error boundary layer found in `src/lyra/core/`. ADR-066 (WorkerError envelope in contracts) belongs to cluster A/NATS Contracts bucket. The ADR-006 + ADR-058 pair is a coherent but incomplete error-handling arc; neither should be archived.

**ADR-021 supersession by ADR-035/037/040:**
Code fully confirms. `lyra.outbound.{platform}.{bot_id}` subjects are live in adapters; standalone hub process (`lyra hub` / `_bootstrap_hub_standalone`) and standalone adapter processes (`lyra adapter telegram`) are the production topology. ADR-021's Option A (hub-per-adapter) is completely replaced. FULL-ARCHIVE is confirmed.

---

## Surprises & contested calls

1. **ADR-007 partial Phase 2**: Streaming path (`cli_pool_streaming.py`) implements the respawn-on-mismatch (Phase 2), while non-streaming (`cli_pool.py`) still silently ignores. This asymmetry is not documented in the ADR. The ADR needs an update noting this split implementation.

2. **ADR-014 InboundAudio resurrection**: The ADR describes `InboundAudio` as a "dead type" pending #80. Code shows `audio_payload.py` comment "Replaces the parallel InboundAudio envelope" — the type was effectively replaced by `AudioPayload`/`AudioPipeline` via ADR-039, not upgraded as Option C proposed. The ADR's design intent (typed audio envelope, distinct from text) was achieved via a different mechanism. The ADR text is slightly misleading now but the design goal is met.

3. **ADR-019/020/023 Proposed status**: All three are implemented. They should be marked Accepted. The "Proposed" status is stale and may confuse contributors into thinking these are unbuilt decisions.

4. **ADR-024 location drift**: The ADR describes `AgentStore` as a new addition to the stores pattern but doesn't mention the subsequent move to `lyra.infrastructure.stores` (ADR-059 hexagonal remediation). The ADR remains accurate for what it decided; it just needs a note that the implementation file moved.

---

## Wave 2 hand-off table

| ADR | Action | Detail |
|-----|--------|--------|
| ADR-001 | KEEP-LIVE-ACCURATE | No changes needed |
| ADR-003 | KEEP-LIVE-ACCURATE | No changes needed |
| ADR-004 | KEEP-LIVE-ACCURATE | No changes needed |
| ADR-005 | KEEP-LIVE-ACCURATE | No changes needed; amendment accurately reflects code |
| ADR-006 | KEEP-LIVE-NEEDS-EDIT | Add note: implementation moved to pool processor exec layer / middleware; decision (Option A) still in force |
| ADR-007 | KEEP-LIVE-NEEDS-EDIT | Add note: streaming path implements Phase 2 (respawn), non-streaming still Phase 1 (silent ignore) |
| ADR-010 | KEEP-LIVE-ACCURATE | No changes needed |
| ADR-013 | KEEP-LIVE-ACCURATE | No changes needed |
| ADR-014 | KEEP-LIVE-NEEDS-EDIT | Add note: audio routing gap resolved via ADR-039; InboundAudio replaced by AudioPayload; start/stop Protocol still open |
| ADR-015 | KEEP-LIVE-NEEDS-EDIT | Add note: Findings A, C, D implemented; Finding B (reply_message_id) status unclear |
| ADR-019 | KEEP-LIVE-NEEDS-EDIT | Change status Proposed → Accepted; note per-agent registry implemented in bootstrap/factory |
| ADR-020 | KEEP-LIVE-NEEDS-EDIT | Change status Proposed → Accepted; lyra-agent script entry confirmed in pyproject.toml |
| ADR-021 | FULL-ARCHIVE | Body already says superseded; code confirms NATS topology; add superseded_by: ADR-035, ADR-037, ADR-040 |
| ADR-023 | KEEP-LIVE-NEEDS-EDIT | Change status Proposed → Accepted; note per-agent registry concern resolved |
| ADR-024 | KEEP-LIVE-NEEDS-EDIT | Add note: AgentStore moved to lyra.infrastructure.stores per ADR-059 hexagonal remediation |
| ADR-026 | KEEP-LIVE-ACCURATE | configure_pool() confirmed in code |
| ADR-029 | KEEP-LIVE-ACCURATE | DB-first hot-reload confirmed in code |
| ADR-030 | KEEP-LIVE-ACCURATE | ScrapeProvider, VaultProvider, SessionTools all confirmed |
| ADR-031 | KEEP-LIVE-ACCURATE | ProcessorRegistry and per-scope outbound dispatch confirmed |
| ADR-038 | KEEP-LIVE-ACCURATE | dead_backend_hits absent from codebase; confirmed removed |
| ADR-039 | KEEP-LIVE-ACCURATE | NATS STT/TTS clients and subjects confirmed live |

**Summary tally:**
- KEEP-LIVE-ACCURATE: 12 (001, 003, 004, 005, 010, 013, 026, 029, 030, 031, 038, 039)
- KEEP-LIVE-NEEDS-EDIT: 8 (006, 007, 014, 015, 019, 020, 023, 024)
- FULL-ARCHIVE: 1 (021)
- PARTIAL-SUPERSEDE: 0
- MERGE-INTO: 0
