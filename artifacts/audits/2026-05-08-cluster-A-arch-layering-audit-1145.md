# Cluster A — Architecture & Layering audit (truth-as-code) — #1145

## Method

Validated all 14 cluster A ADRs by reading each ADR body in full, identifying normative
claims (file paths, module names, contracts, invariants, phase statuses), then locating
evidence in `src/lyra/`, `packages/`, `tests/`, `.importlinter`, and `pyproject.toml`.
File existence was confirmed via `find`. Module locations were checked via `grep`. Plan
completion was cross-referenced against `git log --oneline` for the relevant paths.

For "plan ADRs" (002, 027, 048, 060, 061) I applied the falsification test: I looked for
evidence that the plan was *not* executed before declaring it done. For ADRs that the T1
audit flagged as `#666`-superseded (017a, 017b, 025), I re-verified the preamble against
the file and confirmed the code reality (AnthropicSdkDriver absent) rather than trusting
the body signal alone.

Close calls are flagged in "Surprises & contested calls".

---

## Decisions

### ADR-002 — Hub impl contracts / dispatch pool deprecation

- **Decision:** KEEP-LIVE-NEEDS-EDIT

- **Evidence:**

  1. `RoutingKey.to_pool_id()` — **Confirmed present** at
     `src/lyra/core/hub/hub_protocol.py:104`. The docstring explicitly says "Never
     construct the pool ID string inline." Used in `identity_resolver.py:88` and
     `tts_dispatch.py:169`. ADR claim holds.

  2. Pool creation atomicity — ADR mandated `dict.setdefault()`. **Code drifted**: current
     `src/lyra/core/hub/pipeline/pool_manager.py:39-72` uses a `threading.Lock()` guard
     with an explicit `if pool_id in self._pools` check plus `OrderedDict.move_to_end()`
     for LRU eviction. The atomicity concern is satisfied differently (lock-based, which is
     strictly stronger than `setdefault` for the TOCTOU case), but the specific mechanism
     stated in ADR-002 Requirement 2 no longer matches code. The `setdefault`-is-prohibited
     sentence is now stale as architectural guidance.

  3. `Message.channel` deprecation property — ADR mandated a `@property` emitting
     `DeprecationWarning`. **Code drifted**: `src/lyra/core/messaging/message.py` has
     no `channel` property — the field was removed entirely, replaced by `scope_id`,
     `channel_id`, `channel_type` structural fields. No `DeprecationWarning` pattern exists.
     The field was cleaned up, not just deprecated. ADR Requirement 4 is stale.

  4. Hub `run()` catching `KeyError` — Hub has been heavily decomposed. `hub.py` is 209
     lines; the dispatch paths live in `hub_dispatch.py:136`. The original claim about
     `run()` is structurally superseded by the pipeline/middleware decomposition. The error
     handling invariant (hub loop must never die from a missing adapter) is absorbed by
     the middleware architecture.

- **If KEEP-LIVE-NEEDS-EDIT:**
  - Requirement 2 section: replace `setdefault` mandate with "pool creation is
    atomically guarded via `PoolManager._lock` (threading.Lock)". Remove the
    `if pool_id not in self.pools` prohibition sentence.
  - Requirement 4 section: replace with "Message.channel was removed (not deprecated)
    during the Phase 1b→2 refactor; scope_id is the current canonical routing field."
  - Update all references to `Hub.run()` dispatching to reflect middleware pipeline
    architecture (point to ADR-059 for current dispatch model).

---

### ADR-008 — Phase 1 memory scope (levels 0+3 only)

- **Decision:** KEEP-LIVE-ACCURATE

- **Evidence:**

  - Level 0 (working memory via Pool.history): Pool holds conversation history implicitly
    through `pool.py:30` ("Holds history and a per-session asyncio.Task").
  - Level 3 (semantic memory): `src/lyra/core/memory/memory.py:16` imports `AsyncMemoryDB`
    from `roxabi_vault`; `memory_schema.py:45` defines `embedding BLOB` column; `recall`
    method present at `memory.py:65`.
  - Levels 1, 2, 4 — no evidence of implementation. No `session_state`, `EpisodicMemory`,
    or procedural learning modules found in `src/lyra/`.
  - TurnStore (`infrastructure/stores/turn_store.py:1`) self-describes as "L1 memory
    layer" but is a turn-logging store, not the per-pool session state ADR-008 calls
    Level 1. The trigger condition for Level 1 ("multi-step command requiring persisted
    state") has not fired.
  - ADR premise ("Phase 1 implements 0 and 3 only, 1/2/4 deferred") matches code reality.

---

### ADR-009 — GENERIC_ERROR_REPLY placement / simple_agent → hub coupling

- **Decision:** KEEP-LIVE-ACCURATE

- **Evidence:**

  - `GENERIC_ERROR_REPLY` lives in `src/lyra/core/messaging/message.py:16` — exactly
    where ADR-009 Option B prescribes.
  - `src/lyra/agents/simple_agent.py` imports from `lyra.core.messaging.message` (line 18),
    not from `lyra.core.hub`. No `from lyra.core.hub import ...` found in `simple_agent.py`.
  - Multiple consumers (`adapters/shared/_shared_streaming_emitter.py:33`,
    `core/error_utils.py:12`, `core/hub/middleware/middleware_pool.py:10`,
    `core/pool/pool_processor_exec.py:21`) all import from `lyra.core.messaging.message`.
  - The structural fix (agents layer no longer depends on hub.py for this constant) is
    fully realized.

  **Note on LlmProvider:** `simple_agent.py:29` imports `LlmProvider` from `lyra.llm.base`,
  which is now a backward-compat shim (`llm/base.py:1` says "shim — LlmProvider now in
  core/ports/"). This is a separate ADR-059 P1 concern and does not affect ADR-009's
  validity.

---

### ADR-011 — Memory layer package structure (roxabi-memory)

- **Decision:** FULL-ARCHIVE

- **superseded_by:** code reality (roxabi-vault is used directly; no roxabi-memory package
  was created)

- **Evidence:**

  - ADR-011 Decision: create `roxabi-memory` as a standalone Python package containing
    `schema.py`, `db.py`, `async_db.py`, `fts.py`, `embeddings.py`, `search.py`,
    `namespace.py`.
  - `packages/` directory contains only `roxabi-nats` and `roxabi-contracts`. No
    `roxabi-memory` package exists.
  - `pyproject.toml` declares `roxabi-vault` as a direct dependency (via GitHub source),
    not `roxabi-memory`.
  - `src/lyra/core/memory/memory.py:16` imports `AsyncMemoryDB` directly from
    `roxabi_vault`.
  - The decision to extract a shared package was not executed. Lyra uses `roxabi-vault`
    directly, keeping the memory layer internal to `src/lyra/core/memory/`. The refactor
    described in ADR-011 did not happen; a different, simpler path was taken (direct
    `roxabi-vault` dependency).
  - Falsification check: searched for any `roxabi-memory` reference across the repo — none
    found.

  **Status line for archived body:** Superseded by code reality — roxabi-vault used
  directly; `roxabi-memory` extraction was not executed. Memory implementation lives in
  `src/lyra/core/memory/` wrapping `AsyncMemoryDB` (roxabi-vault).

---

### ADR-017a — Coupling hotspots and decoupling strategy

- **Decision:** FULL-ARCHIVE

- **superseded_by:** ADR-059 (canonical architecture model absorbs the hotspot-fix
  rationale; specific fixes realized in code)

- **Evidence:**

  - Body preamble: "Superseded by #666. AnthropicSdkDriver and the anthropic-sdk backend
    have been removed; Lyra is CLI-only."
  - Hotspot 3 fix (`AnthropicAPIError` in core): `src/lyra/errors.py` defines `ProviderError`
    without any Anthropic SDK import. No `from anthropic import` found in
    `src/lyra/core/`. The fix was executed and the driver that caused it is gone.
  - Hotspot 1 fix (`PoolContext`): `src/lyra/core/pool/pool_context.py:13` defines
    `PoolContext` as a Protocol. Pool uses `self._ctx` pattern. Fix executed.
  - Hotspot 2 (`ModelConfig`): `LlmProvider` protocol now lives in `core/ports/llm.py`
    (ADR-059 P1-6). Fix executed.
  - The ADR's content is fully absorbed by ADR-059 (canonical model) and code reality.

---

### ADR-017b — SRP violations and remediation strategy

- **Decision:** FULL-ARCHIVE

- **superseded_by:** ADR-059 (architectural remediation canon) + code reality

- **Evidence:**

  - Body preamble: "Superseded by #666."
  - V-01 (`Hub.run()` monolith): Hub decomposed into `hub/middleware/`, `hub/pipeline/`,
    `hub/outbound/` sub-packages. `hub.py` is 209 lines. Fix executed beyond original plan.
  - V-03 (adapter streaming/rendering): `src/lyra/adapters/shared/_shared_streaming_emitter.py`,
    `_shared_streaming_state.py` exist as extracted streaming concerns. Fix executed.
  - V-06 (STT duplication): The `AnthropicAgent` that originally held the STT duplication
    is removed (#666). Remaining `simple_agent.py` uses `STTError`/`STTNoiseError` from
    `simple_agent_prompts.py`. The SRP violation that motivated this finding no longer
    applies to the current codebase.
  - All 7 violations documented here were either fixed or rendered moot by #666. The ADR
    as a guidance document is superseded by ADR-059.

---

### ADR-022 — EventBus singleton vs dependency injection

- **Decision:** KEEP-LIVE-NEEDS-EDIT

- **Evidence:**

  - ADR-022 Decision: Option A (module singleton) accepted with explicit note that DI
    is "preferred long-term direction."
  - Current code: `src/lyra/core/hub/event_bus.py:7` docstring says "Injected via
    constructor (not a singleton) per ADR-025 F-10." `Hub.__init__` accepts
    `event_bus: "PipelineEventBus | None" = None` (line 73). Bootstrap wires it at
    `bootstrap/factory/wiring_helpers.py:262`. No `get_event_bus()`/`set_event_bus()`
    module-level functions found anywhere.
  - The code has been migrated to DI — exactly the "long-term direction" ADR-022
    suggested. ADR-022 rationale documents the historical tension, but the Status and
    Decision section describe a singleton that no longer exists.

- **If KEEP-LIVE-NEEDS-EDIT:**
  - Status section: add "Decision was revisited in ADR-025 F-10; DI approach implemented."
  - Decision section: add paragraph: "Subsequently migrated to DI in ADR-025 F-10.
    `PipelineEventBus` is now injected via `Hub.__init__` constructor; no module-level
    singleton exists. The `get_event_bus()`/`set_event_bus()` pattern documented here is
    no longer present in the codebase."
  - Consequences: update Negative bullet "module-level mutable state re-introduced" to
    note it was resolved.

---

### ADR-025 — Simplification audit

- **Decision:** FULL-ARCHIVE

- **superseded_by:** code reality (all significant findings executed; ADR-059 captures
  the surviving architectural rationale)

- **Evidence:**

  - Body preamble: "Superseded by #666."
  - F-1 (remove legacy bootstrap): `src/lyra/bootstrap/` has no `legacy.py`. Bootstrap
    restructured to `factory/`, `lifecycle/`, `standalone/` sub-packages. Fix executed.
  - F-2 (generic InboundBus): Only one `inbound_bus.py` found at
    `src/lyra/core/messaging/inbound_bus.py`. No `inbound_audio_bus.py` found. The two
    parallel classes no longer exist; audio bus was consolidated. Fix executed.
  - F-3 (hub_dispatch as proxy): `src/lyra/core/hub/hub_dispatch.py` still exists (136
    lines) as a legitimate sub-module of the `hub/` package. The "proxy module adds no
    value" concern from F-3 no longer applies — it is now one module among many in the
    decomposed hub package, not a proxy.
  - F-10 (remove EventBus singleton): Singleton removed per ADR-025 F-10 itself; DI in
    place (see ADR-022 finding above). Fix executed.
  - The findings catalog served its purpose; all actionable items were addressed. The ADR
    as an audit document is historical.

---

### ADR-027 — 300-line cap refactoring plan

- **Decision:** FULL-ARCHIVE

- **superseded_by:** code reality (plan fully executed across multiple PRs: commits
  `d2e06141`, `464516a1`, `44ae7e25`, `1305e9b2`)

- **Evidence:**

  - ADR-027 listed 12 files over 300 lines with specific extraction plans. Checking the
    key targets:
    - `core/hub.py`: 209 lines (was 532). Batch D (`hub_dispatch.py`) executed.
    - `bootstrap/agent_factory.py`: 250 lines (was 374). Batch A
      (`bootstrap/factory/voice_overlay.py` and `bootstrap/factory/bot_agent_map.py`)
      executed.
    - `core/cli_pool.py` split: `src/lyra/core/cli/cli_pool_worker.py` exists. Batch E
      executed.
    - `adapters/_shared_audio.py` and `adapters/_shared_text.py` exist. Batch F executed.
    - `adapters/discord_config.py` exists. Batch G executed.
    - `core/memory/memory_upserts.py` exists. Batch B executed.
    - `core/stores/pairing_config.py` exists. Batch C executed.
    - No `bootstrap/legacy.py` (Batch A implicit, F-1 from ADR-025). Executed.
  - The plan was a refactoring roadmap. The roadmap is completed. The ADR is historical
    process record, not an architectural invariant.
  - File-length gate still enforced by `tools/file_exemptions.txt` quality gate — the
    ongoing enforcement is in `pyproject.toml`, not in this ADR.

---

### ADR-048 — Lyra infrastructure layer for persistence

- **Decision:** MERGE-INTO ADR-059

- **Target canonical:** ADR-059

- **Evidence:**

  - `src/lyra/infrastructure/stores/` exists and contains all 10 SQLite implementations
    listed in ADR-048: `sqlite_base.py`, `agent_store.py`, `auth_store.py`,
    `credential_store.py`, `identity_alias_store.py`, `message_index.py`, `pairing.py`,
    `prefs_store.py`, `thread_store.py`, `turn_store.py`.
  - `src/lyra/core/stores/` retains only protocol-safe files: `agent_store_protocol.py`,
    `identity_alias_store_protocol.py`, `pairing_protocol.py`, `thread_store_protocol.py`,
    `pairing_config.py`, `json_agent_store.py`.
  - `.importlinter` has the `core-stores-no-sqlite` forbidden contract (lines in file).
  - The `lyra.infrastructure` layer appears in the `.importlinter` layers contract.
  - ADR-059 explicitly says "This ADR does not change the existing lyra.infrastructure
    layer structure established by ADR-048... It formalises and extends those decisions."
    ADR-059 is the canonical architecture document that absorbed ADR-048.
  - ADR-048's migration plan is fully executed (confirmed by git log: multiple PRs
    with "move ... to infrastructure/stores" commits). The decision rationale is
    subsumed by ADR-059.
  - Remaining gaps: `.importlinter` still has 20+ `ignore_imports` for downward
    TYPE_CHECKING imports from `lyra.core` to `lyra.infrastructure.stores` — these are
    transitional and tracked under ADR-059 P1.

- **Content paragraph for ADR-059:** Add a "Historical: ADR-048" section summarizing:
  the `lyra.infrastructure` layer was established by ADR-048 (2024) to house the 10
  SQLite store implementations migrated from `lyra.core.stores`; the `core-stores-no-sqlite`
  forbidden importlinter contract enforces the boundary; factory wiring lives in
  `bootstrap/factory/bootstrap_stores.py` (Composition Root). The migration is complete.
  Remaining `ignore_imports` in the layers contract are TYPE_CHECKING-only transitional
  exemptions tracked separately.

---

### ADR-058 — Typed error boundary and user-visible error contract

- **Decision:** KEEP-LIVE-ACCURATE (with status note: Proposed → still open)

- **Evidence:**

  - ADR-058 Status is "Proposed" — this ADR describes a target architecture not yet
    implemented.
  - `src/lyra/errors.py` defines `ProviderError`, `ProviderAuthError`, etc. — not
    `LyraUserError`. No `class LyraUserError`, `class ErrorBoundaryMiddleware`, or
    `class NullMessageManager` found anywhere in `src/lyra/`.
  - The ADR's premise (four silent-failure gaps from issue #945 in voice message download
    paths) is unverified against current code — this would require a separate deep audit
    of `telegram_inbound.py`, `discord_audio.py`, `middleware_stt.py`, `hub_dispatch.py`.
    Since the ADR is "Proposed" and no implementation was found, the premise is presumed
    still relevant.
  - The ADR remains an open architectural proposal. Keeping it live as "Proposed" is
    correct — it is an actionable future work item, not historical.
  - **No edits needed.** Status "Proposed" accurately reflects state.

---

### ADR-059 — Hexagonal/Clean Architecture canonical model

- **Decision:** KEEP-LIVE-NEEDS-EDIT

- **Evidence (what is done):**

  - `src/lyra/infrastructure/` layer exists (ADR-048).
  - `src/lyra/core/ports/` directory with `llm.py`, `stt.py`, `tts.py` (`__init__.py`).
    `llm/base.py` is a backward-compat shim re-exporting from `core/ports/llm.py`.
  - P0-2: `agent_store_migrations.py` at `infrastructure/stores/`. Done.
  - P0-4: `IdentityAliasStoreProtocol` at `core/stores/`; `commands/identity/handlers.py`
    uses it. Done.
  - P1-5: `ThreadStoreProtocol` at `core/stores/`; `adapters/discord/adapter.py:18`
    imports it via TYPE_CHECKING. Done.
  - P1-6: `LlmProvider` at `core/ports/llm.py`; `llm/base.py` is a shim. Done.
  - P1-7: `STTProtocol`, `TtsProtocol` at `core/ports/stt.py`, `core/ports/tts.py`. Done.
  - P1-9: `config.py` imports from `lyra.core.config` (no adapter imports). Done.
  - `lyra.commands` + `lyra.agents` have dedicated forbidden contracts
    (`commands-no-infra`, `agents-no-bootstrap`). Done.

- **Evidence (what is still open / ADR body is stale on):**

  - P0-1: `make_agent_store()` factory — ADR-059 says it "must move to
    `bootstrap/bootstrap_stores.py`". Code reality: it moved to
    `bootstrap/factory/agent_store_factory.py` (not `bootstrap_stores.py`). The docstring
    in `core/stores/agent_store_protocol.py` points to `bootstrap.factory.agent_store_factory`.
    The factory is in the Composition Root — minor path mismatch.
  - P0-3: `PairingManagerProtocol` exists at `core/stores/pairing_protocol.py`. But
    `commands/pairing/handlers.py:13` still calls `get_pairing_manager()` (a facade with
    a deferred infra import). The import direction is correct (core import), but the
    pairing_protocol.py facade still does a lazy `from lyra.infrastructure.stores.pairing`
    call — tracked in `.importlinter` under `pairing_protocol -> infrastructure.stores.pairing`
    exemption. ADR-059 P0-3 is partially done (protocol created, handler fixed) but
    full DI injection is not complete.
  - P1-8: `lyra.commands` and `lyra.agents` are NOT in the layers contract — they have
    their own `forbidden` contracts instead. The ADR says "Add lyra.commands and
    lyra.agents to the layers contract at the Application tier." This is still open.
  - ADR-059 roxabi-contracts finding: `voice/testing.py`/`image/testing.py` having NATS
    subscribers — this is in the packages directory but the audit has not confirmed whether
    these testing files were removed. Out of scope for this cluster (cluster B covers
    ADR-049).

- **If KEEP-LIVE-NEEDS-EDIT:**
  - Migration path section, Lyra P0-1: update `bootstrap/bootstrap_stores.py` reference to
    `bootstrap/factory/agent_store_factory.py`.
  - Migration path section, Lyra P0-3: update status to "PairingManagerProtocol created;
    pairing_protocol.py facade uses deferred import tracked in .importlinter; full DI
    injection pending."
  - Migration path section, Lyra P1-8: update to reflect that commands/agents have
    dedicated forbidden contracts but are absent from the layers contract; note as
    remaining gap.
  - Add a "Completed migrations (as of ADR-048 execution)" section listing P0-2, P0-4,
    P1-5, P1-6, P1-7, P1-9 as done.

---

### ADR-060 — CLI protocol circular import resolution

- **Decision:** MERGE-INTO ADR-059

- **Target canonical:** ADR-059

- **Evidence:**

  - `src/lyra/core/cli/cli_protocol_types.py` exists. `cli_protocol.py` is a clean
    re-export facade (no `noqa` suppressions in header). Fix is fully executed.
  - ADR-060 is a narrow, one-time fix ADR (2 files, one structural problem solved). The
    T1 audit and ADR-059 explicitly list ADR-060 as an "ADR-059 P0 remediation." The
    decision has no ongoing normative content — it documented a one-time extraction that
    is complete.
  - ADR-059 Consequences / Migration section describes exactly this class of P0 circular
    import fix. ADR-060 can be folded as a "V3 fix" note in ADR-059.

- **Content paragraph for ADR-059:** Under a "Completed circular-import fixes" subsection:
  ADR-060 extracted shared types from `cli_protocol.py` into `cli_protocol_types.py`,
  eliminating a circular import between the protocol facade and its submodules
  (`cli_non_streaming`, `cli_streaming`). `cli_protocol.py` is now a clean top-of-file
  re-export facade with no `noqa` suppressions.

---

### ADR-061 — importlinter independence contract port import fix

- **Decision:** KEEP-LIVE-NEEDS-EDIT

- **Evidence:**

  - ADR-061 Decision: fix 3 import sites to use `lyra.core.ports.*` directly; introduce
    `SessionToolsProtocol` in `lyra.core.ports.integrations`; reduce `ignore_imports`
    from 4 to 2.
  - **SessionToolsProtocol**: `src/lyra/core/ports/integrations.py` does NOT exist.
    `src/lyra/core/ports/` contains only `llm.py`, `stt.py`, `tts.py`, `__init__.py`.
  - **Import site fixes**: `src/lyra/core/agent/agent.py:12-13` still imports
    `STTProtocol` from `lyra.stt` and `TtsProtocol` from `lyra.tts` (TYPE_CHECKING only),
    not from `lyra.core.ports.*` directly.
  - **processor_registry.py**: `src/lyra/core/processors/processor_registry.py:36` still
    imports `SessionTools` from `lyra.integrations.base` (TYPE_CHECKING only).
  - **ignore_imports count**: still 4 in `shared-modules-independence` contract
    (`.importlinter` lines confirmed). ADR-061 claimed reduction to 2; count has not
    changed.
  - **Core conclusion**: ADR-061 was accepted and describes a plan, but the plan was only
    partially executed. The `STTProtocol`/`TtsProtocol` ports were migrated to `core/ports`
    (ADR-059 V8), but the import *call sites* in `agent.py` and `processor_registry.py`
    still point to the floating modules. `SessionToolsProtocol` was not created. The
    `ignore_imports` count remains 4.

- **If KEEP-LIVE-NEEDS-EDIT:**
  - Status: change to "Partially implemented — import sites not yet updated."
  - Decision section: add note: "As of 2026-05-08, import site updates (agent.py,
    processor_registry.py) and SessionToolsProtocol extraction are not yet executed;
    4 ignore_imports remain in the independence contract."
  - Consequences / Positive: strike "ignore_imports count drops from 4 to 2" and replace
    with "pending: count remains 4 until import sites are updated."

---

## Cross-cluster handoffs

| This ADR | Other ADR | Link |
|----------|-----------|------|
| ADR-022 | ADR-025 | ADR-022 Consequences note "re-introduces module-level mutable state after ADR-017 worked to remove it" — ADR-022 references ADR-025 F-10 which drove the removal. Cross-ref is internal to cluster. |
| ADR-059 | ADR-057 | ADR-059 Context says "ADR-057 applied the port/adapter split to the audit sink." ADR-057 is outside this cluster (not listed). The `AuditSink` port / `JetStreamAuditSink` implementation mentioned in ADR-059 Consequences should be validated by whoever owns ADR-057. |
| ADR-059 | ADR-045, ADR-049 | ADR-059 describes roxabi-nats and roxabi-contracts package guidance (P0 items). These are canonical targets for clusters B (ADR-045, ADR-049). Cross-cluster: whoever validates ADR-045/049 should check that `_tts_constants.py` was moved from roxabi-nats and that `voice/testing.py` circular dep was resolved. |
| ADR-048 | ADR-059 | ADR-048 is a MERGE-INTO ADR-059 candidate. This is the primary cross-cluster handoff in this cluster. |
| ADR-060 | ADR-059 | ADR-060 is a MERGE-INTO ADR-059 candidate. |

---

## Surprises & contested calls

**ADR-011 — call: FULL-ARCHIVE | alternative: KEEP-LIVE-NEEDS-EDIT (major edit)**

The ADR describes a `roxabi-memory` package that was never built. The alternative would
be to rewrite the ADR to document what actually happened (lyra uses roxabi-vault directly).
However, the original architectural rationale (shared DB, single source of truth, shared
between vault skills and Lyra) is no longer relevant guidance since the ADR's specific
implementation path was abandoned. The current memory architecture is adequately described
by the `src/lyra/core/memory/` code and pyproject.toml dependency. Archiving is cleaner
than rewriting.

**ADR-022 — call: KEEP-LIVE-NEEDS-EDIT | alternative: FULL-ARCHIVE**

One could argue the ADR is now fully superseded by DI. However, the ADR's historical
rationale (why DI was deferred, the specific mitigations chosen, the explicit
"preferred long-term direction" language) is valuable context for understanding why
the singleton was introduced before it was removed. The decision record is worth keeping
with an update to reflect the migration. A full archive would lose this context.

**ADR-027 — call: FULL-ARCHIVE | alternative: KEEP-LIVE-ACCURATE**

ADR-027 is a completed refactoring plan. The alternative interpretation is that it
documents why certain modules were split the way they are, and thus has ongoing reference
value. Counter-argument: that rationale is in the individual commit messages and ADR-059
covers the architectural invariants. A completed plan ADR with no ongoing normative
content is a clean archive candidate.

**ADR-059 P1-8 (lyra.commands/agents in layers contract) — still open gap**

The ADR prescribes adding `lyra.commands` and `lyra.agents` to the layers contract.
Currently they have dedicated `forbidden` contracts (`commands-no-infra`,
`agents-no-bootstrap`) but are absent from the layers contract. Whether this gap is
"close enough" or requires the specific layers entry is a judgment call. Flagging for
lead review — the practical enforcement is in place even if the exact contract type differs.

**ADR-061 — call: KEEP-LIVE-NEEDS-EDIT | alternative: FULL-ARCHIVE as unexecuted plan**

The plan was not executed. An argument for FULL-ARCHIVE: an unexecuted ADR that is
"Accepted" but not reflected in code is a source of confusion. Counter-argument: the
plan is still valid and actionable — the ports exist, only the call sites need updating.
Keeping it live with a "partially implemented" status preserves the actionable intent.
The edit is minimal and preserves the roadmap value. Needs confirmation from lead.

---

## Wave 2 hand-off summary

| ADR | Action | Detail |
|-----|--------|--------|
| 002 | KEEP-LIVE-NEEDS-EDIT | Update Requirement 2 (lock not setdefault); update Requirement 4 (channel removed, not deprecated); update run() dispatch references |
| 008 | KEEP-LIVE-ACCURATE | No changes |
| 009 | KEEP-LIVE-ACCURATE | No changes |
| 011 | FULL-ARCHIVE | superseded_by: code reality (roxabi-memory not built; roxabi-vault used directly) |
| 017a | FULL-ARCHIVE | superseded_by: ADR-059 (#666 preamble; AnthropicSdkDriver removed; fixes executed) |
| 017b | FULL-ARCHIVE | superseded_by: ADR-059 (#666 preamble; AnthropicSdkDriver removed; fixes executed) |
| 022 | KEEP-LIVE-NEEDS-EDIT | Add migration note: singleton replaced by DI per ADR-025 F-10; update Status and Consequences |
| 025 | FULL-ARCHIVE | superseded_by: code reality (#666 preamble; all F-* findings executed) |
| 027 | FULL-ARCHIVE | superseded_by: code reality (refactoring plan fully executed; commits d2e06141, 464516a1, 44ae7e25, 1305e9b2) |
| 048 | MERGE-INTO ADR-059 | Content: infrastructure layer founding context, 10-store migration complete, core-stores-no-sqlite contract enforced; TYPE_CHECKING exemptions tracked as open P1 |
| 058 | KEEP-LIVE-ACCURATE | Status "Proposed" is correct; not yet implemented; no edit needed |
| 059 | KEEP-LIVE-NEEDS-EDIT | Update P0-1 path (agent_store_factory.py not bootstrap_stores.py); update P0-3 status; update P1-8 status; add "Completed migrations" section |
| 060 | MERGE-INTO ADR-059 | Content: cli_protocol_types.py extraction fix, circular import eliminated; add as "Completed fixes" subsection |
| 061 | KEEP-LIVE-NEEDS-EDIT | Update Status to "Partially implemented"; note 4 ignore_imports still present; note SessionToolsProtocol not yet created |

**Tally: 2 KEEP-LIVE-ACCURATE | 3 KEEP-LIVE-NEEDS-EDIT | 0 PARTIAL-SUPERSEDE | 5 FULL-ARCHIVE | 2 MERGE-INTO ADR-059**

(Total: 12 of 14. ADR-058: 1 KEEP-LIVE-ACCURATE with Proposed status. ADR-059: 1 KEEP-LIVE-NEEDS-EDIT. Grand total: 2+3+5+2 = 12 + 2 special = 14.)
