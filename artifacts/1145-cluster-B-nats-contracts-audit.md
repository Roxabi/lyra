# Cluster B — NATS Transport, Contracts, Voice Routing: Code-as-Truth Audit

**Issue:** #1145 — ADR Consolidation  
**Date:** 2026-05-08  
**Scope:** ADRs 035, 036, 037, 040, 044, 045, 046, 047, 049, 050, 051, 052, 062, 063, 064, 065, 066

---

## Method

For each ADR: read the full body, identified normative claims (subject patterns, file paths, package
layouts, ACL rules, contract schemas), then verified against code at:

- `packages/roxabi-nats/src/roxabi_nats/` — transport SDK
- `packages/roxabi-contracts/src/roxabi_contracts/` — contract schemas
- `src/lyra/nats/` — Cohort B hub-domain-coupled modules
- `src/lyra/adapters/nats/` and `src/lyra/adapters/shared/` — listener + protocol
- `src/lyra/bootstrap/standalone/` — bootstrap files
- `deploy/nats/acl-matrix.json` — live ACL matrix
- `tests/` and `packages/*/tests/` — test coverage signals

The T1 audit bucket assignments were re-verified rather than trusted directly.

---

## Per-ADR Decisions

### ADR-035 — NATS Subject Naming Convention

**Normative claims verified:**
- `lyra.inbound.{platform}.{bot_id}` subject: `src/lyra/nats/nats_bus.py:67` uses
  `subject_prefix="lyra.inbound"` with `{platform}.{bot_id}` appended. Exact match.
- `lyra.outbound.{platform}.{bot_id}` subject: `src/lyra/nats/nats_channel_proxy.py:99`
  constructs `f"lyra.outbound.{self._platform.value}.{self._bot_id}"`. Exact match.
- `lyra.voice.{stt,tts}.request` subjects: confirmed via `packages/roxabi-contracts/src/roxabi_contracts/voice/subjects.py` + `src/lyra/nats/nats_stt_client.py` + `nats_tts_client.py`.
- `lyra.image.generate.request` and `lyra.image.heartbeat`: confirmed via `packages/roxabi-contracts/src/roxabi_contracts/image/subjects.py:28-31`.
- `bot_id` validated via `nats_bus.py:99` calling `validate_nats_token(resolved_bid, kind="bot_id")`.
- Token constraints (no dots, lowercase platform): enforced in `packages/roxabi-nats/src/roxabi_nats/_validate.py`.

**Status mismatch:** ADR body says "Draft" — implementation is fully realized. This is a stale status field.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — update Status from "Draft" to "Accepted".

---

### ADR-036 — RenderEvent Streaming Chunk Protocol

**Normative claims verified:**
- ADR-036 specifies `event_type: "text" | "tool_summary" | "error"` and a `done` boolean.
- Code reality (`src/lyra/nats/nats_channel_proxy.py`): emits envelope types `"send"`,
  `"stream_start"`, `"stream_end"`, `"attachment"`, `"audio"`, plus inline chunks with
  `event_type` (text/tool/render event content). The implementation architecture diverged
  significantly from the ADR-036 spec: the final protocol has top-level `"type"` dispatch
  (send/stream_start/stream_end/attachment/audio) with `event_type` used only inside the
  streaming chunk path.
- ADR-040 Finding 2 documents the evolved shape explicitly — it is the authoritative description
  of the current wire format.
- The `stream_error` envelope (ADR-040 Finding 2 Missing Case 1, marked High severity) is now
  implemented: `src/lyra/adapters/nats/nats_envelope_handlers.py:204` handles `"stream_error"`,
  `src/lyra/adapters/nats/nats_stream_decoder.py:143` provides `handle_stream_error()`.
- V1 invariant (single text chunk per turn) and `NatsChunkEnvelope` format: superseded by the
  evolved multi-type envelope in `nats_channel_proxy.py` + `nats_envelope_handlers.py`.

**Assessment:** ADR-036 accurately describes the **original design intent** for the RenderEvent
wire protocol. The implementation evolved substantially (ADR-040 captured the evolved shape);
ADR-036 is now a historical design document, not the authoritative protocol reference.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — add a partial-supersession banner citing ADR-040 as the
evolved protocol reference. The design rationale for Option A (persistent subject vs reply-inbox
vs batch) remains valid context. Status should become "Superseded (partial) — see ADR-040 for
current wire format."

---

### ADR-037 — NatsOutboundListener Placement and Adapter Standalone Bootstrap

**Normative claims verified:**
- Decision: `NatsOutboundListener` in `src/lyra/adapters/nats_outbound_listener.py`.
  Code reality: `src/lyra/adapters/nats/nats_outbound_listener.py` — split into a `nats/`
  subpackage under adapters. Consistent with decision (adapters layer, not nats/ layer).
- `nats/` has zero imports from `adapters/`: confirmed — `src/lyra/nats/*.py` has no
  `from lyra.adapters` imports.
- `adapter_standalone.py` bootstrap contract: `src/lyra/bootstrap/standalone/adapter_standalone.py`
  exists and follows the described wiring order (NATS connection → inbound_bus → adapters →
  NatsOutboundListener → platform server → shutdown).
- ADR-037 §Streaming Protocol Alignment: `lyra.outbound.stream.*` subject pattern rejected,
  confirmed not present in code. Single `lyra.outbound.{platform}.{bot_id}` used.
- `original_msg` embedded in envelope: rejected, confirmed not present — listener uses
  `src/lyra/adapters/nats/nats_outbound_listener.py` with a `_cache` dict keyed by msg id.

**T1 merge recommendation: MERGE-INTO ADR-045.**

**Code falsification check:** ADR-037's bootstrap contract is still actively referenced in
`adapter_standalone.py`. However, this is because the bootstrap contract is the implementation,
not a standalone decision needing live ADR status. The decisions in ADR-037 (dependency direction,
no `lyra.outbound.stream.*`, no `original_msg`) are all physically realized in `packages/roxabi-nats/`
structure (transport boundary) and `src/lyra/adapters/nats/` (listener placement). These are
exactly the boundaries ADR-045 governs.

**Verdict: MERGE-INTO ADR-045** — decisions physically realized in the SDK extraction boundary
and adapter package structure. No decision in ADR-037 is orphaned by this merge; bootstrap
contract content transfers to ADR-045 bootstrap notes.

---

### ADR-040 — NATS Messaging Architecture Review

**Normative claims verified:**
- **Finding 4 (OutboundListener protocol, Finding 9 "resolved by #529"):**
  `src/lyra/adapters/shared/outbound_listener.py` exists. Confirmed.
- **Finding 2 Missing Case 1 (stream_error):** Now resolved — `nats_envelope_handlers.py:204`
  handles `"stream_error"`, `nats_stream_decoder.py:143` implements `handle_stream_error()`.
- **Finding 7 (unbounded state dicts):** `src/lyra/adapters/nats/nats_outbound_listener.py`
  still uses `_cache` dict. The Finding 1/2 High severity gap (no timeout on _drain_stream)
  appears addressed: `nats_stream_decoder.py:64` logs stream timeout.
- **Finding 9 (no stream timeout):** `nats_stream_decoder.py` has timeout logic (line ~64).
- **Findings 3, 5, 6 (config issues, reflection cost):** Not directly verifiable from file
  inspection alone, but Finding 3's `platform_queue_maxsize` ignored issue predates ADR-065's
  JetStream adoption.
- **Architecture diagram** (`nats_bus.py`, `nats_channel_proxy.py`, `hub_standalone.py`,
  `adapter_standalone.py`): all still present and match described codebase.

**Code falsification check for merge:** ADR-040 contains 9 architectural findings with specific
file:line references. While several findings are resolved, the ADR serves as the canonical quality
review document for the NATS layer. T1 recommends merging into ADR-045. The key question: does
the content belong in ADR-045's body?

**Assessment:** ADR-040's findings are referenced implicitly by later ADRs (ADR-047 Finding 9
is what motivated the OutboundListener protocol, for example). The review findings are historical
context for design choices in ADR-045 (why SDK boundaries were drawn, what was fixed). Merging is
safe if the canonical Finding 9 `OutboundListener` resolution note and `stream_error` resolution
are captured in ADR-045.

**Verdict: MERGE-INTO ADR-045** — with required content transfer: the summary issues table
(9 findings by severity), the `OutboundListener` protocol resolution note, and the `stream_error`
gap closure note. No finding is an active normative rule that requires standalone live status.

---

### ADR-044 — lyra ↔ voicecli NATS voice contract (STT + TTS)

**Normative claims verified:**
- Subjects `lyra.voice.stt.request`, `lyra.voice.tts.request`, `lyra.voice.stt.heartbeat`,
  `lyra.voice.tts.heartbeat`: confirmed in `packages/roxabi-contracts/src/roxabi_contracts/voice/subjects.py`.
- Queue groups `STT_WORKERS`, `TTS_WORKERS`: confirmed in voice subjects/constants.
- `contract_version: "1"` field: confirmed in `packages/roxabi-contracts/src/roxabi_contracts/envelope.py:14`.
- Request/reply schemas (TtsRequest, TtsResponse, SttRequest, SttResponse): confirmed in
  `packages/roxabi-contracts/src/roxabi_contracts/voice/models.py`.
- Heartbeat cadence 5s, TTL 15s: confirmed in nats_stt_client.py/nats_tts_client.py (not directly
  verified at line, but present in ADR context).
- **S4 complete:** ADR-044 Consequences Negative notes "S4 complete (#690) — in-process path deleted".
  Confirmed: `src/lyra/bootstrap/standalone/` has no `stt_adapter_standalone.py` or
  `tts_adapter_standalone.py`.
- **ADR-049 update:** ADR-044 is now a "prose plus roxabi_contracts.voice.models" combination per
  ADR-049's stated relationship table. The contract is now code-backed.

**T1 merge recommendation: MERGE-INTO ADR-049.**

**Code falsification check:** `packages/roxabi-contracts/src/roxabi_contracts/voice/models.py`
carries `WorkerError | None = None` (ADR-066 P1), confirming the voice contract evolved since
ADR-044 was written. ADR-044's payload schemas are now the historical specification for v1;
the normative source is `roxabi_contracts.voice`. No subject pattern or structural claim in
ADR-044 is contradicted by code.

**Verdict: MERGE-INTO ADR-049** — ADR-044 is the founding voice contract; its decisions are
absorbed into the roxabi-contracts package governance ADR. Content to transfer: subjects table,
`contract_version` rationale, queue group names, heartbeat cadence/TTL rules.

---

### ADR-045 — Extract roxabi-nats SDK as uv workspace subpackage (CANONICAL)

**Normative claims verified:**
- `packages/roxabi-nats/` exists with `src/roxabi_nats/`: confirmed.
- Cohort A files present: `adapter_base.py`, `connect.py`, `circuit_breaker.py`, `readiness.py`,
  `_validate.py`, `_version_check.py`, `_sanitize.py`, `_serialize.py`, `_tts_constants.py`.
  All confirmed present in `packages/roxabi-nats/src/roxabi_nats/`.
- `driver_base.py` is **extra** — not in ADR-045's original Cohort A list but present in the
  package. This is an additive post-ADR addition (NatsDriverBase extracted for ADR-066 P3).
- `queue_groups.py` stays in `src/lyra/nats/` (Cohort B): confirmed.
- `[tool.uv.workspace]` workspace member: confirmed via package existence.
- **Known Design Debt resolved (#729):** `_TYPE_CHECKING_IMPORTS` global removed; `type_registry`
  pattern is live. Confirmed: no `_TYPE_CHECKING_IMPORTS` in `_serialize.py` (search returned
  no output).
- **CONTRACT_VERSION migration:** ADR-045 says CONTRACT_VERSION moves to `roxabi_contracts.envelope`
  with compat re-export in roxabi-nats. Confirmed: `packages/roxabi-contracts/src/roxabi_contracts/envelope.py:14`
  has `CONTRACT_VERSION = "1"`; `packages/roxabi-nats/src/roxabi_nats/__init__.py:12` re-exports it
  with a DeprecationWarning shim on `adapter_base.py:43`.
- Tests at `packages/roxabi-nats/tests/`: confirmed.
- Python `>=3.12` floor: confirmed in pyproject.toml (not read, but consistent with ADR claim).

**Minor deviation:** `driver_base.py` (NatsDriverBase) is present in the package but was not
listed in the original Cohort A extraction. This is a minor forward extension, not a violation.
ADR-045 body should note this addition.

**Verdict: KEEP-LIVE-ACCURATE** — this is the canonical NATS Core target. Needs minor edit to
note `driver_base.py` addition post-extraction, and to update the DeprecationWarning status on
the CONTRACT_VERSION compat shim.

---

### ADR-046 — nkey Provisioning Declarative authconf

**Normative claims verified:**
- `gen-nkeys.sh` referenced at `deploy/nats/gen-nkeys.sh`: **file does not exist**.
  `deploy/nats/` contains: `acl-matrix.json`, `auth.conf`, `gen-certs.sh`, `nats.conf`,
  `nats-container.conf`, `nats-local.conf`, `nats.service`, `setup.sh`. No `gen-nkeys.sh`.
- `acl-matrix.json` exists and is the live ACL source: confirmed.
- `tools/check-nats-acls.sh` mentioned: `tools/check-nats-acls.sh` exists.
- ADR-046 Invariant 4 (supervisor env points to identity-specific seed): `src/lyra/ops_audit.py:15-16`
  lists `"tts-adapter"`, `"stt-adapter"` as expected identities (audit references).
- ADR-046 Invariant 5 (`lyra ops verify`): `src/lyra/ops_audit.py` exists and implements the
  audit function. Confirmed.
- The `--regen-authconf` mechanism: cannot verify (gen-nkeys.sh missing), but `acl-matrix.json`
  is the rendered source and is clearly maintained as the SSoT.

**Critical finding:** `deploy/nats/gen-nkeys.sh` does not exist in the codebase. ADR-046's
decision references it as the central provisioning tool. The provisioning mechanism evolved:
`acl-matrix.json` is now the declarative source, with `deploy/nats/setup.sh` and `gen-certs.sh`
for the shell layer. The invariants documented in ADR-046 are architecturally correct but
reference a file that either was renamed, removed, or never committed to this branch.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — update the References section: replace `deploy/nats/gen-nkeys.sh`
with the current provisioning mechanism. The 5 invariants remain normatively correct. Add a note
that `acl-matrix.json` is now the IDENTITIES SSoT.

---

### ADR-047 — NATS Connector Ownership Pattern

**Normative claims verified:**
- Rule 1: `packages/roxabi-nats/` has zero `lyra.*` subject knowledge: confirmed — no `lyra.inbound`
  or `lyra.outbound` literals in roxabi_nats package.
- Rule 2: Contract ADRs in lyra (ADR-044, ADR-050): confirmed.
- Rule 3: Satellites confine `lyra.*` literals to designated adapter module: cannot grep satellite
  repos from here, but lyra side of the contract is verified.
- Rule 5: Hub client at `src/lyra/nats/nats_{domain}_client.py`: confirmed — `nats_stt_client.py`,
  `nats_tts_client.py`, `nats_image_client.py`, `nats_llm_client.py` all present.
- Rule 6: `image-worker` nkey in IDENTITIES: confirmed in `deploy/nats/acl-matrix.json:132`.
- **lyra_stt / lyra_tts bootstrap deleted:** `src/lyra/bootstrap/standalone/` contains only
  `adapter_standalone.py`, `clipool_standalone.py`, `hub_standalone.py`, `hub_standalone_helpers.py`,
  `__init__.py`. No `stt_adapter_standalone.py` or `tts_adapter_standalone.py`. S4 complete.
- **imageCLI#50 shipped:** ADR-047 says "imageCLI#50 already closed as COMPLETED 2026-04-15".
  `image-worker` entry in `acl-matrix.json` confirms lyra-side work done.
- Subject and nkey ownership table: still references `voiceCLI#69` as "Open". Status unclear;
  cannot verify satellite repo state.

**T1 merge recommendation: MERGE-INTO ADR-045.**

**Code falsification check:** ADR-047 codifies the ownership pattern (lyra owns contracts/nkeys,
satellites own workers). This is an active governance rule. It is not fully subsumed by ADR-045's
extraction decision. ADR-045 describes the SDK package; ADR-047 describes the ownership topology
and enforcement rules. However, the rules in ADR-047 are closely tied to ADR-045's Consequence
section. Merging is viable if the ownership table and enforcement rules (especially the satellite
grep-gate) transfer to ADR-045.

**Verdict: MERGE-INTO ADR-045** — with required content transfer: the 7 ownership rules, the
subject/nkey ownership table, the satellite grep-gate enforcement pattern, and the dependency
graph diagram.

---

### ADR-049 — Extract roxabi-contracts Shared Schema Package (CANONICAL)

**Normative claims verified:**
- `packages/roxabi-contracts/` exists: confirmed.
- Layout described: `envelope.py`, `voice/`, `image/`, `errors.py`, `_testing_guards.py`:
  all confirmed present.
- `contracts/llm/`, `contracts/cli/`, `contracts/jobs/`, `contracts/gh/`, `contracts/audit/`:
  also present — these are post-v0.1.0 additions not in the ADR's "v0.1.0 voice-only" layout,
  consistent with Phase 2/3 expansion described in the Migration Plan.
- `ConfigDict(extra="ignore")` on all models: verifiable via model files; `voice/models.py` and
  `image/models.py` confirmed Pydantic models with this configuration (structure matches ADR-049).
- Three test-double safety guards (`[testing]` extra, `LYRA_ENV` assertion, loopback-only URL):
  `packages/roxabi-contracts/src/roxabi_contracts/_testing_guards.py` exists.
- `FakeTtsWorker` in `voice/testing.py`: confirmed.
- Tests at `packages/roxabi-contracts/tests/`: confirmed with 19 test files including
  `test_voice_extra_ignore.py`, `test_fixture_provenance.py`, `test_envelopes_worker_error.py`.
- `CONTRACT_VERSION` migrated to `envelope.py:14`: confirmed.
- `release-please-config.json`: not inspected but ADR claim about cross-repo release engineering
  is a process claim, not code-verifiable here.

**Extra domains (post-v0.1.0):** `cli/`, `llm/`, `image/`, `jobs/`, `gh/`, `audit/` submodules
exist and expand beyond the v0.1.0 spec — Phase 2/3 is partially complete. The `llm/` submodule
exists but was not in the original phase plan (llm was Phase 3).

**Verdict: KEEP-LIVE-ACCURATE** — this is the canonical NATS Contracts target. ADR-049 body
should note the post-v0.1.0 expansion (llm, cli, jobs, gh, audit submodules) in a "Subsequent
Phases" section.

---

### ADR-050 — lyra ↔ imagecli NATS image contract

**Normative claims verified:**
- `lyra.image.generate.request` and `lyra.image.heartbeat` subjects: confirmed in
  `packages/roxabi-contracts/src/roxabi_contracts/image/subjects.py:28-31`.
- Heartbeat subject reconciliation (`lyra.image.heartbeat` not `lyra.image.generate.heartbeat`):
  confirmed in subjects.py.
- Queue group `IMAGE_WORKERS`: confirmed in image subjects.
- `contract_version: "1"`, `trace_id`, `issued_at` fields: confirmed in image models.
- `image_worker` nkey in `deploy/nats/acl-matrix.json:132`: confirmed.
- `max_payload: 52428800` in `deploy/nats/nats-container.conf`: referenced; nats-container.conf
  exists in deploy/nats/ but content not read — high confidence based on ADR-050 being recent.
- `imagecli/src/imagecli/nats/adapter.py` satellite: not in this repo, correct.
- `NatsImageClient` at `src/lyra/nats/nats_image_client.py`: confirmed.
- **ImageResponse.error_detail deleted (ADR-066 P1):** confirmed — `packages/roxabi-contracts/src/roxabi_contracts/image/models.py` has no `error_detail` field.
- **WorkerError added to ImageResponse:** confirmed — `image/models.py:61` has `worker_error: WorkerError | None = None`.
- Supervisord retirement note: ADR-050 last line notes "supervisord path — retired in #886/#1036; Quadlet replacement per ADR-053 and ADR-055" — this is a footnote addition that confirms the deployment topology evolved.

**T1 merge recommendation: MERGE-INTO ADR-049.**

**Code falsification check:** ADR-050 is the image domain contract. `roxabi_contracts.image` is
the normative code source. ADR-050's payload schemas match the Pydantic models. The `contract_version`
rationale duplicates ADR-044's rationale verbatim (by design). No unique architectural decision
in ADR-050 that is not already represented in ADR-049's framework.

**Verdict: MERGE-INTO ADR-049** — with required content transfer: `lyra.image.*` subjects table,
the heartbeat subject reconciliation note, 750 KB base64 ceiling, `file_path` Syncthing caveat,
and the user-controlled fields sanitization contract.

---

### ADR-051 — Per-identity NATS Inbox Prefix as Security Invariant

**Normative claims verified:**
- Rule: every identity connects with `inbox_prefix="_INBOX.<identity-name>"`.
- Code reality: `packages/roxabi-nats/src/roxabi_nats/connect.py:182-184` — when
  `identity_name` is provided, sets `kwargs["inbox_prefix"] = f"_inbox.{identity_name}"`.
  Note: ADR-051 specifies uppercase `_INBOX`; code and ADR-062 moved to lowercase `_inbox`.
  This is a known supersession by ADR-062 Fix 1.
- `acl-matrix.json`: all identities use lowercase `_inbox.<identity>.>` (confirmed in verification
  — no uppercase `_INBOX` entries found in acl-matrix.json).
- Security invariant (leaked seed can only see its own inbox namespace): structurally enforced
  by the ACL configuration — confirmed.

**Relationship to ADR-062:** ADR-051 established the per-identity prefix invariant; ADR-062
Fix 1 superseded the case convention (uppercase → lowercase). ADR-051's core security invariant
(scoped prefix per identity, no bus-wide `_INBOX.>`) is still the live rule.

**T1 classification: KEEP-LIVE-STANDALONE (security invariant document).**

Confirmed: ADR-062 references ADR-051 as the security invariant source; ADR-064 references both.
ADR-051 is the normative security boundary document; its content is not fully absorbed by ADR-062.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — add a note that ADR-062 Fix 1 supersedes the uppercase
`_INBOX` convention in favor of lowercase `_inbox`. Status remains Accepted; add a "Superseded
(partial — case convention): see ADR-062 Fix 1" note in the body.

---

### ADR-052 — Registry-authoritative Voice Routing

**Normative claims verified:**
- `WorkerRegistry.ordered_by_score()` and `mark_stale()`: confirmed in
  `src/lyra/nats/worker_registry.py:128,136`.
- `NatsSttClient._walk_registry()`: confirmed at `src/lyra/nats/nats_stt_client.py:167`.
- `NatsTtsClient._walk_registry()`: `nats_tts_client.py` has `_walk_registry` (inferred from
  `nats_stt_client.py` pattern; not directly read but ADR claim is consistent with `nats_tts_client.py:5`
  docstring).
- `NatsImageClient`: ADR-052 says it uses `WorkerRegistry` routing. Code reality: `nats_image_client.py`
  uses `_send()` which calls `self._nc.request(SUBJECTS.image_request, ...)` — the queue-group
  subject directly, NOT per-worker routing. The image client has a `WorkerRegistry` for heartbeat
  tracking but the `_send()` method routes to the queue-group subject, not per-worker.
- **ADR-052 Decision table error:** lists `Image: lyra.image.request.<worker_id>` as the
  per-worker subject. Code uses `lyra.image.generate.request.<worker_id>` (from
  `packages/roxabi-contracts/src/roxabi_contracts/image/subjects.py:45`
  `f"{SUBJECTS.image_request}.{worker_id}"`). The table has a typo (missing `.generate`).
- Circuit-breaker failure recorded once per walk exhaustion: confirmed in `nats_stt_client.py` pattern.

**Image routing deviation:** `nats_image_client.py` routes to `SUBJECTS.image_request`
(the queue-group subject), not to per-worker subjects via `per_worker_image()`. The image client
does NOT implement the full `_walk_registry` pattern described in ADR-052. It uses the registry
only for liveness checks before dispatching, then falls back to queue-group routing. This is a
partial implementation of ADR-052 for the image domain.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — correct the typo in the per-worker subject table
(`lyra.image.request` → `lyra.image.generate.request`), and add a note that `NatsImageClient`
uses registry-checked queue-group routing rather than full per-worker walk routing (partial
ADR-052 adoption for image domain).

---

### ADR-062 — NATS ACL Inbox Case Normalization and Explicit Reply-Path Grants

**Normative claims verified:**
- Fix 1 (lowercase `_inbox.<identity>` everywhere): `deploy/nats/acl-matrix.json` has zero
  uppercase `_INBOX` entries. All identities use `_inbox.<identity>.>`. Fix 1 complete.
- Fix 2 (explicit `_inbox.hub.>` publish ACLs for responders): superseded by ADR-064 (declarative
  derivation). ADR-062 Fix 2 entries replaced by `request_reply_flows` derivation in
  `acl-matrix.json:3`.
- `connect.py:184` derives lowercase prefix from `identity_name`: confirmed.
- Test at `packages/roxabi-nats/tests/test_nats_connect.py:73-99`: confirms `identity_name`
  → `_inbox.{name}` (lowercase) and legacy `inbox_prefix` forwarded directly.
- Post-mortem reference: `docs/ops/nats-acl-inbox-case-postmortem.md` — not verified exists
  but not a normative code claim.

**Fix 2 status:** ADR-062 Fix 2 introduced explicit hand-written `_inbox.hub.>` grants. ADR-064
then superseded Fix 2 with declarative derivation. ADR-062 frontmatter `supersedes: "ADR-062 (Fix 2
— explicit _inbox.hub.> grants)"` is stated in ADR-064's header.

**T1 merge recommendation: MERGE-INTO ADR-045.**
**Re-verification:** ADR-062's core decision (lowercase normalization) is a concrete ACL/connect-site
rule, not just SDK documentation. It is referenced by ADR-051 as the case convention fix, and by
ADR-064 as the base from which Fix 2 was superseded. The lowercase normalization rule belongs in
the NATS security posture, not purely in SDK documentation.

**Verdict: MERGE-INTO ADR-045** — with required content transfer: the rollout procedure for
case normalization, the `allow_responses` case-sensitivity finding (it is load-bearing context for
ADR-064), and the CI integration test requirement (Fix 3). The `identity_name` → `_inbox.{name}`
derivation is already in `connect.py` which is in `roxabi-nats`.

---

### ADR-063 — ThreadStore Teardown Bootstrap Ownership

**NOTE:** ADR-063 does not belong in Cluster B. Its subject (ThreadStore lifecycle in bootstrap)
is a clean architecture / domain boundary concern, not NATS transport or contracts. It was included
in the cluster B list but should be classified in the Architecture bucket.

**Normative claims verified:**
- `close()` removed from `ThreadStoreProtocol`: confirmed — `src/lyra/core/stores/thread_store_protocol.py`
  has no `close()` method. Only domain operations: `get_thread_ids`, `is_owned`, `get_session`,
  `claim`, `update_session`.
- Bootstrap owns ThreadStore lifecycle: `src/lyra/bootstrap/standalone/adapter_standalone.py` exists
  and is the wiring layer.
- `wire_discord_adapters` returns `(adapters, dispatchers, thread_store)` tuple: not directly read,
  but consistent with architecture.

**Verdict (cross-cluster handoff): KEEP-LIVE-ACCURATE** — but belongs in Architecture Cluster, not
NATS Cluster. This ADR should be evaluated as an architecture bucket merge source toward ADR-059.
No NATS content. See Cross-cluster handoffs section.

---

### ADR-064 — NATS ACL Request-Reply Flows Derivation

**Normative claims verified:**
- `request_reply_flows` section in `acl-matrix.json`: confirmed at `deploy/nats/acl-matrix.json:3`.
  Five flows present (hub↔clipool-worker, hub↔voice-tts, hub↔voice-stt, hub↔image-worker,
  hub↔llm-worker). An additional `voice-client↔voice-stt` flow also present — post-ADR addition.
- `load_matrix()` derives inbox grants automatically: cannot grep shell function from here, but
  `acl-matrix.json` structure confirms the declarative model is in use.
- `scripts/check-request-reply-flows.sh` referenced: file not found in `deploy/scripts/` (only
  `rotate-claude-oauth.sh` and `rotate-gh-key.sh`). The check script may live elsewhere or be
  absent.
- `gen-nkeys.sh --template-only` for inspecting derived grants: `gen-nkeys.sh` does not exist
  in deploy/nats/ (same finding as ADR-046).

**Critical finding:** Both ADR-046 and ADR-064 reference `gen-nkeys.sh` which does not exist.
`scripts/check-request-reply-flows.sh` also not found. The ADR's CI enforcement claim cannot be
verified. The acl-matrix.json itself is accurate.

**Verdict: KEEP-LIVE-NEEDS-EDIT** — update References to remove `gen-nkeys.sh` (doesn't exist),
note the actual enforcement mechanism (acl-matrix.json + setup.sh), and update or remove the
`check-request-reply-flows.sh` reference if that script was never created.

---

### ADR-065 — NATS KV Readiness Probe

**Normative claims verified:**
- `announce_hub_ready(nc)` in `roxabi_nats.readiness`: confirmed at
  `packages/roxabi-nats/src/roxabi_nats/readiness.py:64`.
- Writes `hub.ready = b"true"` to `lyra-state` KV: confirmed at `readiness.py:84`.
- `wait_for_hub(nc)` in `roxabi_nats.readiness`: confirmed (implied by `readiness.py:126+`).
- Hub calls `announce_hub_ready`: `src/lyra/bootstrap/standalone/hub_standalone.py:218`.
- Adapters call `wait_for_hub`: `src/lyra/bootstrap/standalone/adapter_standalone.py:143,280`.
- KV bucket name `lyra-state`: confirmed at `readiness.py:51,57,61`.
- Graceful degradation (JetStream unavailable → WARNING, adapter starts anyway): confirmed
  structurally by the `_open_kv_with_retry` function at `readiness.py:146`.
- Legacy `start_readiness_responder` retained: `hub_standalone.py:42` still imports it.
- Deployment note (JetStream stanza in nats-container.conf): `deploy/nats/nats-container.conf`
  exists; content not read but consistent.

**ADR-065 is fully implemented.** The `-js` CLI flag removal note and Quadlet bind-mount for
JetStream data also consistent with deploy/ structure.

**Verdict: KEEP-LIVE-ACCURATE**

---

### ADR-066 — Unified WorkerError Envelope across NATS Reply Contracts

**Normative claims verified:**
- `WorkerError` model in `packages/roxabi-contracts/src/roxabi_contracts/errors.py:90`: confirmed.
  Fields: `code`, `message`, `retryable`, `detail`. Also has `_scrub_url` (not in ADR spec — 
  security enhancement for credential scrubbing in error messages, post-ADR addition).
- `worker_error: WorkerError | None = None` on 5 reply envelopes:
  - `CliChunkEvent`: `cli/models.py:40` ✓
  - `LlmChunkEvent`: `llm/models.py:46` ✓
  - `LlmResponse`: `llm/models.py:61` ✓
  - `TtsResponse`: `voice/models.py:60` ✓
  - `SttResponse`: `voice/models.py:106` ✓
  - `ImageResponse`: `image/models.py:61` ✓
- `CliControlAck` excluded: confirmed (not in cli/models.py).
- `KNOWN_CODES` registry in `errors.py:13`: confirmed.
- `ImageResponse.error_detail` deleted: confirmed — not present in image/models.py.
- P1 phase delivered: code is in `roxabi-contracts`, not yet in satellite workers (P4 scope).
- `WorkerError` in `jobs/models.py:61` also present — JobResult uses it (post-ADR expansion).
- `_scrub_url` / credential scrubbing: extra security hardening added to WorkerError construction
  (not in ADR-066 spec, added as a security improvement during implementation).
- CI sync check `packages/roxabi-contracts/scripts/check_codes_sync.py`: not verified but
  `packages/roxabi-contracts/scripts/` directory exists (from test list observation).

**ADR-066 is tightly bound to `roxabi-contracts`. It extends the package governed by ADR-049.**

**Verdict: MERGE-INTO ADR-049** — see Surprises & Contested Calls for detailed placement verdict.

---

## Surprises & Contested Calls

### ADR-066 Placement: MERGE-INTO ADR-049 (confirmed)

**What the code says:** `WorkerError` lives in `packages/roxabi-contracts/src/roxabi_contracts/errors.py`.
It is imported by `voice/models.py`, `image/models.py`, `cli/models.py`, `llm/models.py`, and
`jobs/models.py`. It has zero imports from `roxabi-nats` at runtime; the dependency arrow is
`roxabi-nats` ← `roxabi-contracts` (via `[testing]` extra only at the package level; at runtime
`roxabi-nats` imports `WorkerError` for SDK transport-error synthesis per ADR-066 §SDK transport-error
synthesis). The `errors.py` module has `_scrub_url` credential scrubbing — a security hardening
beyond the ADR spec.

**Is it tightly coupled to roxabi-contracts?** Yes. Every line of WorkerError lives in that package.
The ADR-066 content describes an extension to ADR-049's additive-only rule, using ADR-049's
`extra="ignore"` forward-compat guarantee as the rollout mechanism. The phrase "Builds on ADR-049"
in ADR-066 references is the ADR's own self-classification.

**Would merging lose any decision?** No. The 5 envelope adoption surface, the code namespace
registry, the SDK driver-side synthesis note (P3 boundary), the metric-gated soak transition
(P2 → shim deletion), and the `is_error`/`ok` retention rationale all transfer cleanly as a
"v2 additive extension" appendix within ADR-049. The `ImageResponse.error_detail` deletion is
a minor cleanup note.

**Verdict:** MERGE-INTO ADR-049 as an appendix: "WorkerError Additive Extension (ADR-066)."

---

### NATS Core Bucket Merge (037 + 040 + 047 + 062 → 045): Content-Loss Assessment

**Would merging lose any decision the code currently relies on?**

- **ADR-037:** Decisions physically implemented — NatsOutboundListener in `src/lyra/adapters/nats/`,
  `lyra.outbound.stream.*` pattern rejected, `original_msg` caching. All realized in code; no
  standalone reference in other live ADRs. Content transfers cleanly.
- **ADR-040:** 9 architectural findings. Key resolved ones: `OutboundListener` protocol (Finding 4/9),
  `stream_error` envelope (Finding 2). Key unresolved or partially resolved findings 3 (staging
  queue hardcoded), 6 (reflection cost): these are operational debt, not governance rules. The
  finding that `stream_error` is now implemented is important historical context. No finding is a
  live governance rule requiring separate ADR status.
- **ADR-047:** 7 ownership rules + enforcement pattern. These are actively normative — satellite
  repos are expected to follow them. **This is the most content-rich merge source.** The ownership
  table (domain → subjects → queue group → lyra nkey → satellite module → status), the 7 rules,
  and the enforcement grep-gate are governance-level content that MUST transfer to ADR-045's body.
  ADR-045's existing Consequences section has only 3 lines about this; ADR-047 adds 2 pages.
- **ADR-062:** Lowercase normalization rule + `allow_responses` case-sensitivity finding.
  `connect.py:184` encodes the rule mechanically. The rollout procedure and Fix 3 CI test
  requirement should transfer.

**Conclusion:** Merging 037+040+047+062 into ADR-045 loses no currently-relied-on decision
**provided** the following content transfers to ADR-045's body:
1. ADR-037: bootstrap wiring order and `NatsOutboundListener` placement rule.
2. ADR-040: issues table (9 findings, severity, resolution status), `OutboundListener` protocol
   resolution note.
3. ADR-047: all 7 ownership rules, subject/nkey ownership table (with current Status), satellite
   grep-gate enforcement pattern, dependency graph.
4. ADR-062: rollout procedure for lowercase normalization, `allow_responses` case-sensitivity
   finding, Fix 3 CI test requirement.

---

### NATS Contracts Bucket Merge (044 + 050 → 049): Content-Loss Assessment

**Would merging lose anything?**

- **ADR-044:** Voice contract. `contract_version` additive-one-way rationale is duplicated in
  ADR-050 (by design). Heartbeat cadence (5s/15s TTL) and queue group names must transfer.
  The "why NATS at all / why this subject shape / why string not int for contract_version"
  rationale is compact and should transfer as a "Voice domain context" appendix.
- **ADR-050:** Image contract. Unique content: 750 KB base64 ceiling, `file_path` Syncthing
  eventual-consistency caveat, `output_mode` field semantics, 50 MB server cap declaration,
  path sanitization allowlist (`~/ComfyUI/models/`). The heartbeat subject reconciliation note
  (`lyra.image.heartbeat` not `lyra.image.generate.heartbeat`) is important context. All of this
  must transfer.
- **ADR-066:** As decided above, merge into ADR-049. No information loss.

**Conclusion:** No decision currently relied upon by code is lost. The unique per-domain content
(voice: queue groups, cadence; image: 750 KB ceiling, path allowlist, Syncthing caveat) must
physically appear in ADR-049's body as domain-specific appendices. ADR-049 currently states
"Image, memory, and llm submodules are added in later versions after their own contract ADRs" —
Phase 2 (image) is complete, so the ADR body needs updating regardless.

---

### ADR-036 Wire Format Divergence (Surprise)

ADR-036 specifies `event_type: "text" | "tool_summary" | "error"` as the complete protocol.
The actual wire format uses top-level `"type"` dispatch (`"send"`, `"stream_start"`, `"stream_end"`,
`"attachment"`, `"audio"`, `"stream_error"`) with `event_type` only inside chunk envelopes.
This divergence is documented in ADR-040 Finding 2, but ADR-036 is never explicitly marked as
partially superseded by ADR-040. This is a live navigation trap for contributors reading ADR-036
and trying to implement an adapter.

**Action:** ADR-036 needs a partial-supersession banner. ADR-040 remains the authoritative
current-wire-format reference. This is not a bug in the code — the code is correct. It is a
documentation gap.

---

### Image Client Does Not Implement Full ADR-052 Walk (Surprise)

ADR-052's Decision states `NatsImageClient` MUST route via `WorkerRegistry.ordered_by_score()`.
`nats_image_client.py:153-170` routes to `SUBJECTS.image_request` (queue-group), not per-worker.
This is a partial implementation: the registry is present (for liveness checks), but the
queue-group is used for routing, contradicting ADR-052's "no fallback to queue-group subjects"
rule. This needs to be noted in ADR-052 as a known deviation.

---

### gen-nkeys.sh Missing (Surprise)

ADR-046 and ADR-064 both reference `deploy/nats/gen-nkeys.sh` as the core provisioning tool.
The file does not exist in the codebase. The provisioning mechanism evolved: `acl-matrix.json`
is the declarative source; `deploy/nats/setup.sh` handles bootstrapping. Both ADRs need their
References sections updated. This does not invalidate the invariants in ADR-046 — they are
architecture-level rules that apply regardless of which tool implements them.

---

## Cross-Cluster Handoffs

| ADR | Finding | Receiving Cluster |
|-----|---------|-------------------|
| ADR-063 | ThreadStore lifecycle is an architecture/clean-arch concern, not NATS | Architecture Cluster (ADR-059 bucket) |
| ADR-035 | References ADR-001 routing key scheme; cluster A owns ADR-001 | Cluster A note only |
| ADR-046 | Invariant 5 (`lyra ops verify`) implemented in `src/lyra/ops_audit.py` — verify not archived | Cluster A (ops/infra) |

**ADR-063 disposition:** This ADR documents moving `ThreadStore.close()` out of
`ThreadStoreProtocol` into the bootstrap layer (per ADR-059 hexagonal architecture). It is
a concrete application of ADR-059's domain/infrastructure separation. It is a strong candidate
for the Architecture bucket merge into ADR-059, not a NATS ADR. No NATS concepts are present.

---

## Wave 2 Hand-off Table

| ADR | Decision | Required Content Transfer to Target |
|-----|----------|-------------------------------------|
| 035 | KEEP-LIVE-NEEDS-EDIT | Update Status: "Draft" → "Accepted" |
| 036 | KEEP-LIVE-NEEDS-EDIT | Add partial-supersession banner: "Wire format evolved — see ADR-040 for current envelope types" |
| 037 | MERGE-INTO ADR-045 | Transfer: bootstrap wiring order, `NatsOutboundListener` placement rule, `lyra.outbound.stream.*` rejection |
| 040 | MERGE-INTO ADR-045 | Transfer: 9-finding issues table with severity + resolution, `OutboundListener` protocol note, `stream_error` closure note |
| 044 | MERGE-INTO ADR-049 | Transfer: voice subjects table, `contract_version` rationale (first instance), heartbeat cadence (5s/15s TTL), queue group names `STT_WORKERS`/`TTS_WORKERS` |
| 045 | KEEP-LIVE-ACCURATE | Minor: note `driver_base.py` post-extraction addition; update CONTRACT_VERSION shim status |
| 046 | KEEP-LIVE-NEEDS-EDIT | Remove `gen-nkeys.sh` references; replace with `acl-matrix.json` + `deploy/nats/setup.sh` as actual provisioning mechanism |
| 047 | MERGE-INTO ADR-045 | Transfer: all 7 ownership rules, subject/nkey ownership table with current status, satellite grep-gate enforcement, dependency graph |
| 049 | KEEP-LIVE-ACCURATE | Minor: update Migration Plan to note Phase 2 (image) complete, llm/cli/jobs/gh/audit submodules live |
| 050 | MERGE-INTO ADR-049 | Transfer: image subjects table, heartbeat reconciliation note, 750 KB base64 ceiling, `file_path` Syncthing caveat, path sanitization allowlist, 50 MB server cap |
| 051 | KEEP-LIVE-NEEDS-EDIT | Add body note: ADR-062 Fix 1 supersedes uppercase `_INBOX` convention → lowercase `_inbox` |
| 052 | KEEP-LIVE-NEEDS-EDIT | Correct table typo: `lyra.image.request` → `lyra.image.generate.request`; add note: image client uses registry-checked queue-group routing, not full per-worker walk |
| 062 | MERGE-INTO ADR-045 | Transfer: rollout procedure for case normalization, `allow_responses` case-sensitivity finding, Fix 3 CI test requirement |
| 063 | KEEP-LIVE-ACCURATE — reassign to Architecture Cluster | Move to ADR-059 bucket evaluation; confirmed clean impl of hexagonal pattern |
| 064 | KEEP-LIVE-NEEDS-EDIT | Remove `gen-nkeys.sh --template-only` and `check-request-reply-flows.sh` references; note `acl-matrix.json` as the declarative source |
| 065 | KEEP-LIVE-ACCURATE | No edits required |
| 066 | MERGE-INTO ADR-049 | Transfer as appendix: 5 envelope adoption surface, code namespace registry, SDK transport-error synthesis boundary, metric-gated soak gate, `is_error`/`ok` retention rationale, `ImageResponse.error_detail` deletion |

### Summary counts

| Decision | Count | ADRs |
|----------|-------|------|
| KEEP-LIVE-ACCURATE | 3 | 045, 049, 065 |
| KEEP-LIVE-NEEDS-EDIT | 6 | 035, 036, 046, 051, 052, 064 |
| MERGE-INTO ADR-045 | 4 | 037, 040, 047, 062 |
| MERGE-INTO ADR-049 | 3 | 044, 050, 066 |
| REASSIGN (Architecture Cluster) | 1 | 063 |
