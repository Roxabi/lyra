# Cross-domain consistency audit — 2026-05-09

## Summary

- 3 contradictions
- 4 duplications without canonical owner
- 0 broken cross-links
- 4 missing cross-links
- 2 out-of-scope content blocks
- 2 orphan topics

---

## Findings

### F1: NATS subject table — `lyra.clipool.*` subjects absent from messaging.md — blocker
**Type**: contradiction / missing content
**Files**:
- `messaging.md` (NATS subject naming section, line 56–65) — lists only `lyra.inbound.{platform}.{bot_id}`, `lyra.outbound.{platform}.{bot_id}`, `lyra.llm.request`, `lyra.llm.health.{worker_id}`
- `deployment.md` (NATS Topics section, line 162–170) — lists `lyra.clipool.cmd`, `lyra.clipool.heartbeat`, `lyra.clipool.control` as live production subjects
- `ARCHITECTURE.md` (line 160) — lists `lyra.clipool.cmd`, `lyra.clipool.control`, `lyra.clipool.heartbeat` as live production topics

**Resolution**: `messaging.md` claims to be the SSoT for NATS subject naming but is missing 3 live subjects. Add `lyra.clipool.cmd`, `lyra.clipool.heartbeat`, `lyra.clipool.control` to the table in `messaging.md`. The rows in `deployment.md` and `ARCHITECTURE.md` are then redundant but acceptable as deployment-context summaries (not contradictions once `messaging.md` is complete).

---

### F2: Memory level numbering — security-routing.md uses L1–L5, storage.md and ARCHITECTURE.md use L0–L4 — blocker
**Type**: contradiction
**Files**:
- `security-routing.md` (#memory-isolation section, line 315–322) — "L1 Working | dict in memory, scoped by pool_id" / "L2 Session | Store keyed by (user_id, session_id)" / "L3 Episodic | ~/.lyra/memory/episodic/{user_id}/" / "L4 Semantic | SQLite, WHERE user_id = ? mandatory" / "L5 Procedural | Global"
- `storage.md` (line 23–29) — "Level 0 (working) is the LLM context window" / "Level 3 (semantic) is SQLite + aiosqlite with BM25"
- `ARCHITECTURE.md` (line 585–592) — "Level 0 — Working memory" / "Level 1 — Session memory" / "Level 3 — Semantic" / "Level 4 — Procedural"

**Resolution**: The L0–L4 numbering in `storage.md` and `ARCHITECTURE.md` is canonical (memory scope is owned by `storage.md`). `security-routing.md` uses a shifted L1–L5 scheme that is internally inconsistent with the rest of the codebase. Fix `security-routing.md` to use L0–L4 with a `→ See storage.md` pointer for the full taxonomy.

---

### F3: MemoryEntry schema — security-routing.md documents 5 memory levels with full schema, storage.md owns memory scope — major
**Type**: duplication without canonical owner (and partial contradiction on level names — see F2)
**Files**:
- `security-routing.md` (lines 270–342) — defines `MemoryEntry` with full Python schema, SQL isolation rule, `Storage by level` table, `Counter updates` code block, and `Implementation status` checklist
- `storage.md` (lines 23–29) — owns "Memory scope levels" per the taxonomy but only covers Level 0 and Level 3; MemoryEntry schema absent

**Resolution**: Memory scope is owned by `storage.md`. Move the full `MemoryEntry` schema, SQL isolation rule, `Storage by level` table, and counter-update code from `security-routing.md` into `storage.md`. `security-routing.md` retains only the security invariant ("every query must include `user_id`") with a `→ See storage.md` pointer. The implementation status checklist belongs in `storage.md`.

---

### F4: Trust resolution model — two contradictory descriptions of where auth runs — blocker
**Type**: contradiction
**Files**:
- `security-routing.md` (#auth section, lines 62–75) — "Auth at the Adapter level, **before** the Bus. The message is rejected at the source." with code showing adapter calling `authenticator.resolve(user_id)` and dropping `BLOCKED` messages before `normalize()`
- `security-routing.md` (#auth section, line 34) — "Updated (2026-05-07) — C3 pattern: In the containerized deployment, adapters always forward messages with `trust=PUBLIC` and trust resolution is performed Hub-side by the Authenticator"
- `deployment.md` (line 65) — "Adapters **must never** derive trust level — always send `PUBLIC`. Trust is resolved exclusively by the Hub (C3 pattern)."
- `deployment.md` (Security table, line 105–106) — "C3 — adapters always send PUBLIC, hub resolves via Authenticator | Hub middleware stage 2–3"

**Resolution**: These two descriptions exist in the same `security-routing.md` document. The C3 note (line 34) is a later amendment that partially contradicts the primary solution text (lines 62–75) which still reads as if adapters do auth themselves. The C3 pattern is current truth. Rewrite the `#auth` solution section in `security-routing.md` to lead with C3: adapters do transport-level auth only (HMAC/token), always send `PUBLIC`, and Hub middleware stages 2–3 resolve trust. Remove or clearly strike out the old "auth at adapter level" code block, or mark it as pre-C3 historical context.

---

### F5: NATS subject format for worker health — messaging.md vs deployment.md — major
**Type**: contradiction
**Files**:
- `messaging.md` (table, line 59) — "`lyra.llm.health.{worker_id}` | worker → hub | Worker heartbeats"
- `deployment.md` (NATS Topics table, line 169) — "`lyra.clipool.heartbeat` | CliPool → Hub | Periodic worker health announcements"

**Resolution**: These are two distinct subjects (`lyra.llm.health.*` for LLM workers vs `lyra.clipool.heartbeat` for the CliPool process). Both may be correct for different worker types, but neither document cross-references the other, making it appear contradictory. `messaging.md` should add `lyra.clipool.heartbeat` to its subject table (fixing F1) and annotate that `lyra.llm.health.*` is for satellite LLM workers while `lyra.clipool.heartbeat` is for the CliPool subprocess runner.

---

### F6: RoutingKey / pool_id format — messaging.md vs ARCHITECTURE.md — major
**Type**: contradiction
**Files**:
- `messaging.md` (line 42) — "Pool IDs are always derived via `RoutingKey.to_pool_id() -> str`, producing `f\"{platform.value}:{bot_id}:{scope_id}\"`"
- `ARCHITECTURE.md` (line 421) — "Telegram chat 555 → agent `lyra`, pool `telegram:main:chat:555`" and "Discord thread 888 → agent `lyra`, pool `discord:main:thread:888`"
- `ARCHITECTURE.md` (line 422) — note states "`\"main\"` is the legacy single-bot sentinel... replaced with the configured `bot_id`"

**Resolution**: No factual contradiction — `ARCHITECTURE.md` correctly notes `main` is legacy. However, `ARCHITECTURE.md` still uses `telegram:main:chat:555` as the primary example, which will mislead readers who miss the note. The examples in `ARCHITECTURE.md` should be updated to use the current format `telegram:lyra:chat:555` as the canonical example, with `main` only in a historical footnote.

---

### F7: Deployment of auth / trust — duplication across security-routing.md and deployment.md — major
**Type**: duplication without canonical owner
**Files**:
- `security-routing.md` (lines 24–113) — full auth design: TrustLevel enum, Authenticator, GuardChain, config TOML, integration code, admin access, implementation checklist
- `deployment.md` (Security table, lines 103–112) — "Trust levels: `OWNER > TRUSTED > PUBLIC > BLOCKED`", "Trust resolution: C3 — adapters always send PUBLIC, hub resolves via Authenticator | Hub middleware stage 2–3", cross-platform identity, secrets mechanism

**Resolution**: `security-routing.md` is the canonical owner of auth decisions per the taxonomy. `deployment.md`'s Security section is an acceptable deployment-context summary but duplicates the trust-level list and Authenticator description. Replace the trust level detail in `deployment.md` with `→ See security-routing.md` and keep only deployment-specific facts (which container runs Authenticator, where `auth.db` lives).

---

### F8: Middleware pipeline — described in full in both deployment.md and ARCHITECTURE.md — major
**Type**: duplication without canonical owner
**Files**:
- `deployment.md` (Middleware pipeline table, lines 38–50) — 10-stage table: TraceMiddleware → ValidatePlatformMiddleware → ... → SubmitToPoolMiddleware
- `ARCHITECTURE.md` (Inbound Message Pipeline section, lines 263–319) — same 10 stages with full annotations on each stage's behavior

**Resolution**: Neither document cross-references the other. `security-routing.md` and `workers-tooling.md` own different aspects; the middleware pipeline itself spans messaging+security+workers. The 10-stage table should live in one place (likely `ARCHITECTURE.md` which already has the full annotated version), with `deployment.md` linking to it. Alternatively, if `ARCHITECTURE.md` is a navigator-only index, a new section in `workers-tooling.md` is the right home. Pick one owner and add a `→ See X` from the other.

---

### F9: CliPool OAuth token — out-of-scope in workers-tooling.md, cross-listed in deployment.md — minor
**Type**: out-of-scope content
**Files**:
- `workers-tooling.md` (line 26) — "CliPool Claude OAuth token" section fully documents the `CLAUDE_CODE_OAUTH_TOKEN` Podman secret injection, `type=env` exception, and annual rotation
- `deployment.md` (Key invariants, line 241) — "CliPool OAuth token (`type=env` exception) → `workers-tooling.md` (ADR-071)" — correctly cross-links

**Resolution**: This is acceptable. `workers-tooling.md` owns the token mechanism (ADR-071); `deployment.md` cross-links correctly. No action needed — the cross-link exists. Marking minor because the `deployment.md` cross-link is present and correct.

---

### F10: BlobStore documented in storage.md but voice subjects (`lyra.voice.stt.request`, `lyra.voice.tts.request`) appear in adapters.md without link to contracts.md — missing cross-link — minor
**Type**: missing cross-link
**Files**:
- `adapters.md` (lines 100–108) — "AudioPipeline calls `NatsSttClient.transcribe()` and `NatsTtsClient.synthesize()` over NATS request-reply (`lyra.voice.stt.request` / `lyra.voice.tts.request`)" — subject names appear inline
- `adapters.md` (See also, line 152) — "TTS/STT NATS contracts → `contracts.md` (ADR-052)" — link IS present

**Resolution**: Link exists. Not an actual gap. Reclassified — no action needed. (Counted in summary as 0 additional missing cross-links for this finding.)

---

### F11: schema_version on RenderEvent — contradictory location claim — major
**Type**: contradiction
**Files**:
- `ARCHITECTURE.md` (line 391) — "Every hub↔adapter envelope (`InboundMessage`, `InboundAudio`, `OutboundMessage`, `TextRenderEvent`, `ToolSummaryRenderEvent`) carries a `schema_version: int = 1` field. The current version for each envelope lives in a `SCHEMA_VERSION_*` module-level constant in `src/lyra/core/message.py` and `src/lyra/core/render_events.py`."
- `llm-streaming.md` (line 105) — "Every `RenderEvent` subtype carries its own `SCHEMA_VERSION_*` constant." (agrees on per-type constant, but does not contradict on file location)
- `ARCHITECTURE.md` (line 397) — "Note: the outer render-event chunk envelope (`{stream_id, seq, event_type, payload, done}` in `render_event_codec.py`) is itself an implicit, unversioned contract."
- `messaging.md` (lines 71–79) — describes `NatsChunkEnvelope` fields `{stream_id, seq, event_type, payload, done}` without mentioning schema_version

**Resolution**: No direct contradiction between `messaging.md` and `llm-streaming.md` — `messaging.md` correctly notes the outer envelope is unversioned (consistent with `ARCHITECTURE.md`'s note). The `ARCHITECTURE.md` note is the only full description of the versioning mechanism. `llm-streaming.md` and `messaging.md` should each link to `ARCHITECTURE.md` (or a future dedicated section) for the schema_version bump procedure. Currently `messaging.md` has no cross-link to the schema versioning procedure. Missing cross-link: `messaging.md` → schema versioning section in `ARCHITECTURE.md`.

---

### F12: storage.md references `architecture-patterns.md` for hexagonal layer placement, but architecture-patterns.md's file placement rules show `stores/` not `lyra.infrastructure` — minor
**Type**: missing cross-link / minor inconsistency
**Files**:
- `storage.md` (line 17) — "The hexagonal placement of all stores within `lyra.infrastructure` is canonical in `architecture-patterns.md` — not repeated here."
- `architecture-patterns.md` (File Placement Rules, line 237) — shows `stores/ → OUTBOUND ADAPTERS (Storage)` at `src/lyra/stores/sqlite_store.py` — the path is `lyra.stores` not `lyra.infrastructure`
- `architecture-patterns.md` (Engineering Invariants, line 279) — "Infrastructure implements ports; lives in `lyra.infrastructure.*` per ADR-048"

**Resolution**: The File Placement Rules diagram in `architecture-patterns.md` (likely written before ADR-048/059 remediation) shows the pre-refactoring path `src/lyra/stores/`. The Engineering Invariants section below it correctly states `lyra.infrastructure.*`. The top-level diagram is outdated and contradicts the invariant in the same document. Update the File Placement Rules diagram to show `infrastructure/stores/` not `stores/`.

---

### F13: ComplexityEstimator — documented in security-routing.md, belongs in workers-tooling.md or ARCHITECTURE.md — out-of-scope — minor
**Type**: out-of-scope content
**Files**:
- `security-routing.md` (#commands section, lines 219–255) — full `ComplexityEstimator` class definition, `COMPLEXITY_TO_MODEL` mapping table, implementation status

**Resolution**: Per the taxonomy, `security-routing.md` owns "command parser, memory isolation, NATS infra security." Model selection / complexity routing belongs in `workers-tooling.md` (processor registry, routing) or `llm-streaming.md` (LLM config). `CommandParser` ownership in `security-routing.md` is correct; `ComplexityEstimator` and `COMPLEXITY_TO_MODEL` should move to `workers-tooling.md` with a `→ See workers-tooling.md` pointer from `security-routing.md`.

---

### F14: Orphan topic — schema_version bump procedure — orphan — minor
**Type**: orphan topic
**Description**: The full schema_version bump procedure (4 steps: bump constant, update default, coordinate deploy, verify with grep) lives only in `ARCHITECTURE.md` (lines 399–404). None of the 9 domain pages reference it or claim ownership. `messaging.md` owns the chunk envelope (the outer unversioned wrapper); `llm-streaming.md` states every RenderEvent carries a `SCHEMA_VERSION_*` constant but gives no bump procedure. No domain page owns schema versioning discipline.
**Resolution**: Add a brief "Schema versioning" section to `messaging.md` (as the NATS transport SSoT) or `llm-streaming.md` (as the RenderEvent SSoT), covering the 4-step bump procedure. Cross-link from both.

---

### F15: Orphan topic — `RoutingContext` dataclass — orphan — minor
**Type**: orphan topic
**Description**: `RoutingContext` (the per-response routing struct: `channel`, `bot_id`, `chat_id`, `thread_id`, `reply_to_message_id`, `user_id`, `session_id`) is defined in full in `security-routing.md` (#routing section, lines 125–132) and referenced in `ARCHITECTURE.md` (line 775). Neither `messaging.md` (which owns routing key semantics) nor `adapters.md` (which owns adapter dispatch) claims it as canonical. The `messaging.md` scope statement does not list it.
**Resolution**: `messaging.md` should absorb `RoutingContext` as part of routing key semantics (it is the outbound companion to `RoutingKey`), with `security-routing.md` retaining only the security invariant ("adapters verify channel + bot_id before sending") and a `→ See messaging.md`.

---

### F16: Missing cross-link — workers-tooling.md ProcessorRegistry section does not link to security-routing.md CommandParser — minor
**Type**: missing cross-link
**Files**:
- `workers-tooling.md` (ProcessorRegistry section, lines 57–58) — "Slash commands that need conversation history are implemented as `BaseProcessor` subclasses registered via `@register(\"/cmd\")`" — no link to `security-routing.md` which owns `CommandParser`
- `security-routing.md` (#commands section) — owns `CommandParser` and the command routing table

**Resolution**: Add `→ See security-routing.md (CommandParser)` in the `workers-tooling.md` ProcessorRegistry section to clarify that `ProcessorRegistry` handles post-parse execution while `CommandParser` handles pre-parse tokenization.

---

### F17: Missing cross-link — ARCHITECTURE.md memory layer table not linked from storage.md — minor
**Type**: missing cross-link
**Files**:
- `ARCHITECTURE.md` (lines 585–629) — full 5-level memory layer table with implementation status, compaction details, L1 TurnStore details
- `storage.md` (lines 23–29) — covers only L0 and L3; "Levels 1, 2, 4 deferred" with pointer to ADR-008; no link to `ARCHITECTURE.md`

**Resolution**: `storage.md` should add a `→ See ARCHITECTURE.md (Memory Layer)` pointer for readers who need the full 5-level breakdown and historical status.

---

### F18: Missing cross-link — contracts.md voice routing section not linked from adapters.md STT/TTS NATS decoupling section — minor
**Type**: missing cross-link
**Files**:
- `adapters.md` (STT/TTS NATS decoupling section, lines 99–108) — describes `NatsSttClient`/`NatsTtsClient` over `lyra.voice.stt.request` / `lyra.voice.tts.request`, references ADR-039
- `adapters.md` (See also, line 152) — "TTS/STT NATS contracts → `contracts.md` (ADR-052)" — link IS present

**Resolution**: Link exists. Non-finding. (No new action needed; already counted out above.)

---

## Revised Summary (after reclassification)

- 3 blockers (F1 — missing live subjects in messaging.md; F2 — memory level numbering conflict; F4 — auth model self-contradiction in security-routing.md)
- 4 major (F3 — MemoryEntry schema ownership; F5 — worker heartbeat subject ambiguity; F7 — auth duplication across docs; F8 — middleware pipeline duplication; F11 — schema_version procedure missing from domain pages; F12 — outdated file placement diagram)
- 5 minor (F6 — pool_id example uses legacy `main`; F9 — acceptable cross-link; F13 — ComplexityEstimator out-of-scope; F14 — schema_version orphan; F15 — RoutingContext orphan; F16, F17 — missing cross-links)

Consolidated counts per format:
- **3 contradictions** (F2, F4, F12)
- **4 duplications without canonical owner** (F3, F7, F8, F15)
- **0 broken cross-links**
- **4 missing cross-links** (F1 partial, F11, F16, F17)
- **2 out-of-scope content blocks** (F3/F13 — ComplexityEstimator in security-routing.md; MemoryEntry schema in security-routing.md)
- **2 orphan topics** (F14 schema_version bump procedure; F15 RoutingContext)

---

## Verdict

The consolidation is structurally sound: 9 domain pages have well-defined scopes, cross-links are mostly present, and no ADR content appears to be silently lost. However, three blockers prevent declaring this done as SSoT.

The most critical is **F4**: `security-routing.md` contradicts itself — the primary section still presents pre-C3 adapter-level auth while a later inline note says C3 is current truth. A reader following the main flow gets the wrong architecture. This is actively misleading.

**F2** (memory level numbering L0–L4 vs L1–L5) means `security-routing.md`'s memory isolation table is misaligned with `storage.md` — the two documents that should jointly own memory cannot be read together without confusion.

**F1** (three live NATS subjects missing from `messaging.md`) means the self-declared SSoT for subject naming is incomplete; engineers must consult `deployment.md` for a complete picture, defeating the purpose of consolidation.

The major findings (F3, F5, F7, F8, F12) are rot risks: duplicated content in two docs will diverge on the next edit. F8 (middleware pipeline in both `deployment.md` and `ARCHITECTURE.md`) is the highest-probability rot point given how frequently the pipeline changes.

Minor findings are polish. The 9 docs are close to ready but require the 3 blockers fixed before SSoT can be declared.
