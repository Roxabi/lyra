# Cluster D Audit — Deploy, Container/Quadlet, Streaming Pipeline, Brand

**Cluster:** D — 20 ADRs validated
**Date:** 2026-05-08
**Method:** Code-as-truth. Read ADR body → extract normative claims → verify against live code with
citations → apply falsification check → decide.

---

## Method

1. Read all 20 ADR bodies in full.
2. Identified normative claims per ADR.
3. Validated each claim against `deploy/quadlet/*.container`, `deploy/quadlet/*.volume`,
   `deploy/provision.sh`, `.github/workflows/`, `src/lyra/`, `packages/`, and grep output.
4. Applied special verification for the #666-preamble ADRs (012, 016, 018, 028, 032): confirmed
   absence of `AnthropicSdkDriver`, `anthropic_sdk`, `claude_sdk` in all live `.py` files.
   Found only `src/lyra/core/session_lifecycle.py:17` which is a comment referencing the
   pre-#666 state — NOT live code. Full-archive recommendation for 012/016/018 is confirmed.
5. For each archive/supersession call: verified the falsification condition (would the archive be
   wrong if live code still used the ADR's decisions).

---

## Decisions per ADR

### ADR-012 — AnthropicAgent Streaming Dispatch and Dual-Agent Pattern

**Body status:** Accepted. No `#666` preamble.
**Normative claims:** `AnthropicAgent`, `isinstance(result, AsyncIterator)` dispatch at hub,
`anthropic-sdk` backend factory, `AgentBase.process → Response | AsyncIterator`.

**Code check:** `grep -rn "AnthropicSdkDriver|AnthropicAgent|anthropic-sdk" src/ packages/ --include="*.py"` returns zero hits in live code. The dispatch mechanism described (isinstance detection, `_create_agent` factory, `CliPool` as the only backend) is fully replaced. The hub's dispatch path is now NATS-based (`src/lyra/core/hub/`). There is no `AnthropicAgent` class anywhere.

**T1 audit said:** FULL-ARCHIVE. Confirmed.

**Falsification check:** If any live file still used `AnthropicAgent` or the dual-type dispatch, archive would be wrong. No such file exists.

**Decision: FULL-ARCHIVE**

---

### ADR-016 — LLM Provider Protocol and Driver Registry

**Body status:** Accepted. Has `#666` preamble: "AnthropicSdkDriver and the `anthropic-sdk` backend have been removed; Lyra is CLI-only."

**Normative claims:** `LlmProvider` Protocol, `AnthropicSdkDriver`, `ClaudeCliDriver`,
`RetryDecorator`, `CircuitBreakerDecorator`, `ProviderRegistry`.

**Code check:** `AnthropicSdkDriver` — zero hits in live code. `ClaudeCliDriver` — not found under that exact class name; the current architecture is NATS-based (clipool worker in `src/lyra/adapters/clipool/`). The `ProviderRegistry` pattern is gone; driver selection is handled by NATS routing. The `lyra.llm.base` module does not exist.

**T1 audit said:** FULL-ARCHIVE. Confirmed — all structural claims in this ADR refer to removed code.

**Falsification check:** Zero live imports of `LlmProvider`, `ProviderRegistry`, `ClaudeCliDriver` in this form.

**Decision: FULL-ARCHIVE**

---

### ADR-018 — Intermediate Turns, Typing Indicator, Provider Protocol Evolution

**Body status:** Accepted. Has `#666` preamble: "AnthropicSdkDriver and the `anthropic-sdk` backend have been removed; Lyra is CLI-only."

**Normative claims:** `on_intermediate` callback on `LlmProvider.complete()`, `RetryDecorator`/
`CircuitBreakerDecorator` passthrough, `AnthropicSdkDriver.complete()` with `object = None`,
`SimpleAgent` with `_pool._ctx` pattern, `_typing_tasks` dict on `TelegramAdapter`.

**Code check:** `SimpleAgent` as described (with `_pool._ctx` violation) is not in live code —
`src/lyra/adapters/telegram/telegram.py` uses `_typing.cancel()` and `cancel_all()` (confirmed at
`telegram.py:202-210`), not the `_typing_tasks` dict accumulation model. The `on_intermediate`
callback chain described belongs to the pre-#666 architecture.

**T1 audit said:** FULL-ARCHIVE. Confirmed — all six issues documented were either fixed or made
moot by the #666 architecture change.

**Decision: FULL-ARCHIVE**

---

### ADR-028 — Token-Level Streaming Path Shape

**Body status:** Accepted. Has `#666` preamble: same.

**Normative claims:** Parallel streaming path (`send_and_read_stream()`), four edge-case contracts:
1. Cancel-in-flight aclose/reset, 2. Session ID propagation on exhausted generator,
3. Filter `input_json_delta` (discard tool-use JSON fragments), 4. `--include-partial-messages`
spawn-time flag toggle respawn.

**Code check:**

- `--include-partial-messages` is present at `src/lyra/core/cli/cli_protocol_types.py:73`.
- Session ID propagation: `src/lyra/adapters/clipool/clipool_worker.py:254` (`session_id=event.session_id or None`), `:296`, `:331-339` — active in live code.
- `input_json_delta` filtering: `src/lyra/core/cli/cli_streaming_parser.py:28` defines `_DELTA_INPUT_JSON = "input_json_delta"`. However, `render_events.py` now contains `ToolCallArgsRenderEvent` and the ADR-070 implementation surfaces `input_json_delta` events via `ToolCallArgsRenderEvent` (Slice 3 complete). This means **Edge-Case Contract 3 (discard `input_json_delta`)** is superseded by ADR-070.
- Contracts 1, 2, 4 remain normative and in force in live code.

**ADR-070 reference cross-check:** ADR-070 §References explicitly states: "ADR-028 §Edge-Case Contract 3 … Slice 3 (#1100) supersedes that single edge-case contract."

**T1 audit said:** PARTIAL-SUPERSEDE, keep alive with banner. Confirmed — and the banner is warranted precisely because contracts 1, 2, 4 are still load-bearing.

**Falsification check:** If `input_json_delta` were still universally discarded, `ToolCallArgsRenderEvent` could not exist. It does exist (`render_events.py:173`). Contract 3 is correctly superseded.

**Decision: KEEP-LIVE-NEEDS-EDIT**
Add partial-supersession banner: "ADR-028 §Edge-Case Contract 3 superseded by ADR-070 (Slice 3 / #1100). Contracts 1, 2, 4 remain in force."

---

### ADR-032 — LlmEvent → StreamProcessor → RenderEvent Hexagonal Streaming

**Body status:** Accepted. Has `#666` preamble.

**Normative claims:** `LlmEvent → StreamProcessor → RenderEvent` hexagonal pipeline; `LlmEvent`,
`RenderEvent` as frozen dataclasses; `StreamProcessor` in `core/`; `ChannelAdapter` receives
`AsyncIterator[RenderEvent]`; `ClaudeCliDriver.stream()`.

**Code check:**

- `src/lyra/core/messaging/render_events.py` — exists, contains `TextRenderEvent`,
  `ToolSummaryRenderEvent`, `RunStartedRenderEvent`, `RunFinishedRenderEvent`,
  `RunErrorRenderEvent`, `ToolCallStartRenderEvent`, `ToolCallArgsRenderEvent`,
  `ToolCallEndRenderEvent`, `ToolCallResultRenderEvent`.
- `src/lyra/core/messaging/events.py` — exists with `LlmEvent` types.
- `src/lyra/adapters/clipool/clipool_worker.py:20` imports `TextLlmEvent`, `ResultLlmEvent`,
  `ToolUseLlmEvent` from `lyra.core.messaging.events` — the pipeline is live.
- `src/lyra/adapters/shared/_shared_streaming_emitter.py` imports `RenderEvent` and all v2 types.

**The hexagonal pipeline contract is live and in force.** The `#666` preamble refers only to the
`AnthropicSdkDriver` streaming content within the ADR, not the hexagonal pipeline design itself.
ADR-070 explicitly extends this ADR (not replaces it): "ADR-032 gains a 'extended-by ADR-070
(#1097)' cross-reference banner."

**T1 audit said:** PARTIAL-SUPERSEDE (keep hexagonal contract; archive SDK streaming content).
Confirmed — the pipeline architecture is live and referenced by ADR-070 as normative.

**Falsification check:** If the pipeline were dead, `_shared_streaming_emitter.py` would not
import `RenderEvent` types. It does.

**Decision: KEEP-LIVE-NEEDS-EDIT**
Add partial-supersession banner scoped to the SDK-specific content: "The `AnthropicSdkDriver`
streaming path described here was removed in #666. The hexagonal pipeline contract (`LlmEvent →
StreamProcessor → RenderEvent`) remains normative and is extended by ADR-070."

---

### ADR-033 — PuLID Flux2 Face-Lock Strategy

**Body status:** Accepted. No `#666` preamble. No explicit supersession.

**Normative claims:** ComfyUI + PuLID Flux2 node as primary avatar generation path; LoRA training
as long-term complement; `imageCLI` Shape B deferred. Scope: `imageCLI` / brand — not Lyra core.

**Code check:** No reference to PuLID, ComfyUI, or face-lock in `src/lyra/` at all. This ADR's
domain is `imageCLI` and brand workflow, not Lyra's runtime. It describes a decision about an
external tool on Machine 2 (RTX 5070 Ti). ADR-042 explicitly notes: "Artifacts and ADRs that
referenced old `brand/` paths (issues #419 and its children, ADR-033, ADR-034) will be incorrect
after migration. We accept this and do not rewrite history."

**Falsification check:** Is the decision still in force? ComfyUI still exists. The PuLID path is a
machine-level operational decision; no code in lyra implements or contradicts it. But:
- ADR-042 explicitly states ADR-033 references are accepted as pre-migration history.
- Brand assets moved to `~/.roxabi/forge/` per ADR-042.
- ADR-033 is `imageCLI`-scoped, not Lyra core. It has no live Lyra code equivalent.

T1 audit said: discretionary archive candidate. Re-evaluation: this ADR is out-of-scope for Lyra
as a codebase decision (it documents ComfyUI setup on a dev machine). It predates ADR-042 which
supersedes the brand pipeline context. The decision itself (use ComfyUI) is an operational choice
with no Lyra code manifestation.

**Decision: FULL-ARCHIVE**
Rationale: imageCLI/brand scope, not Lyra core. ADR-042 makes brand assets out-of-repo; ADR-033's
context is fully pre-migration. No live Lyra code implements or depends on this decision.
`superseded_by: ADR-042` (brand migration context).

---

### ADR-034 — Brand Asset Pipeline Structure

**Body status:** Explicitly reads "Superseded by ADR-042 (2026-04-10)."

**Code check:** `brand/` directory does not exist in the repository. `src/lyra/` has zero
references to `brand/manifest.json`, `brand/selected/`, or `brand/exploration/`. ADR-042's
migration record confirms all brand assets moved to `~/.roxabi/forge/lyra/brand/`.

**T1 audit said:** FULL-ARCHIVE. Confirmed.

**Decision: FULL-ARCHIVE** — self-declared superseded; `superseded_by: ADR-042`.

---

### ADR-041 — Hub-and-Spoke Supervisor Pattern

**Body status:** Explicitly "Superseded by ADR-047". Body is nearly empty — original content
removed, with a note: "supervisord was retired in favour of Podman Quadlet (ADR-053). Production
is now fully Quadlet — see `deploy/quadlet/*.container` and `systemctl --user`."

**Code check:**
- `deploy/quadlet/` contains `lyra-hub.container`, `lyra-telegram.container`,
  `lyra-discord.container`, `lyra-nats.container`, `lyra-clipool.container` — Quadlet is the
  sole deployment path.
- `deploy/provision.sh` still references `supervisord` at line 469-474 for initial machine setup
  (install check), but this is machine provisioning (install supervisord on the host for other
  non-Lyra projects), not Lyra's runtime deployment.
- `~/projects/CLAUDE.md` confirms supervisord is still in use for non-Quadlet projects; the
  machine-level pattern persists but Lyra is fully Quadlet.
- ADR-041's original content (lyra-owned `deploy/supervisor/` subtree, `make register`,
  `~/projects/conf.d/` symlinks) was removed in PRs #886 and #1036.

**T1 audit said:** FULL-ARCHIVE. Confirmed — the ADR is self-declared superseded and the content
was intentionally gutted. The supervisor pattern for non-Lyra projects is the
machine-level concern documented in `~/projects/CLAUDE.md`, not this ADR.

**ADR-041 verdict: Supervisor pattern is Quadlet-only for Lyra. `supervisord` persists at
machine level for other projects but is irrelevant to this ADR's scope. FULL-ARCHIVE confirmed.**

**Decision: FULL-ARCHIVE** — `superseded_by: ADR-047, ADR-053`.

---

### ADR-042 — Brand Assets Belong in ~/.roxabi/forge

**Body status:** Accepted — supersedes ADR-034.

**Normative claims:** No `brand/` directory in any project repo; canonical location
`~/.roxabi/forge/{project}/brand/`; brand-book loader discovery drops repo fallback paths.

**Code check:** No `brand/` directory exists in the worktree (`ls` shows no brand dir). ADR-042's
migration record (section) lists all steps executed. The brand-book loader in `roxabi-plugins`
was updated per the ADR. No references to `brand/manifest.json` or `brand/selected/` exist in
`src/lyra/`.

**Decision: KEEP-LIVE-ACCURATE** — normative content matches reality; no edit needed.

---

### ADR-043 — roxabi-autodeploy Per-Project Manifests

**Body status:** Proposed.

**Normative claims:** `roxabi-ops` repo with generic Python runner; per-project `deploy/auto-deploy.yml`; replaces `lyra-deploy.timer` and `deploy.sh`.

**Code check:**
- No `deploy/auto-deploy.yml` exists in the Lyra repo.
- No `roxabi-ops` directory, no `autodeploy` script in `deploy/`.
- `deploy/provision.sh:245-256` shows `podman-auto-update.timer` is the active deploy mechanism,
  not `roxabi-autodeploy.timer`.
- The ADR itself notes M16 and M8 as known bugs to fix; M8 is marked "(Resolved in #886)". The
  `deploy.sh` ADR-043 planned to replace is itself retired (per ADR-053's historical note).

**Assessment:** This ADR is "Proposed" and nothing has been built. The problems it addresses
(centralized deploy script, per-project manifest opt-in) are partially solved by `podman
auto-update.timer` + GHCR. The `roxabi-ops` repo does not exist in the projects index
(`~/projects/CLAUDE.md`). ADR-043 describes work that was never implemented and the gap it
addressed was partially closed by a different mechanism.

**T1 audit said:** discretionary archive candidate. Re-evaluation confirms: no implementation, not
planned, the alternative path (Podman auto-update) is operational.

**Decision: FULL-ARCHIVE** — Proposed status, never implemented, superseded by `podman
auto-update.timer` pattern. `superseded_by: ADR-055` (which governs the canonical deploy pattern).

---

### ADR-053 — Deployment Topology and Container Hardening

**Body status:** Accepted, with "Superseded in part by ADR-054" note on Decisions 4+5. Decisions
1, 2, 3 remain in force.

**Normative claims (live):**
- D1: Quadlet as ecosystem-wide strategic target; Lyra as reference implementation.
- D2: Naming normalization to hyphens.
- D3: Image digest pinning (enforcement note: `scripts/deploy-quadlet.sh` retired; current path is
  `podman auto-update`).

**Code check:**
- D1: `deploy/quadlet/` exists with four containers — Quadlet is the production runtime.
  `deploy/provision.sh:245` enables `podman-auto-update.timer`. Confirmed.
- D2: `deploy/quadlet/lyra-hub.container`, `lyra-telegram.container`, `lyra-discord.container`
  all use hyphenated names. Confirmed.
- D3: Image pinning enforcement — `lyra-nats.container` uses `Image=...@sha256:b83efab...` (digest
  pin, confirmed at `lyra-nats.container` lines 5-6). Hub/telegram/discord containers use GHCR
  images via `podman auto-update` (the tag-based approach with auto-update is the current live
  pattern; the `deploy-quadlet.sh` enforcement was retired).
- D4+D5: Superseded by ADR-054.

**T1 audit said:** MERGE-INTO ADR-055 as source. Decision preserved above.

**Contested call:** ADR-053 is the "parent ADR" cited by ADR-055. Merging it in would fold its
D1-D3 content into ADR-055. D1 (Quadlet as strategic target) and D2 (naming) are both accurately
cited by ADR-055 already. D3 (image digest pinning) has moved to `podman auto-update` + GHCR
patterns. The ADR contains significant historical context (why supervisord was kept alongside
Quadlet during migration, ecosystem migration order table) that ADR-055 does not duplicate.

**Decision: MERGE-INTO ADR-055** (per T1 audit). Decisions 1-3 should be preserved in ADR-055's
historical context section.

---

### ADR-054 — Quadlet Credential-Store and UID Rework

**Body status:** Accepted. Supersedes ADR-053 Decisions 4+5.

**Normative claims:**
- D1: `lyra-data.volume` as bind-mount of `%h/.lyra`.
- D2: `UserNS=keep-id` in all three containers (replacing `User=lyra`).
- D3: Adapter data mount `:z` without `:ro`.
- D4: `telegram.env.example` and `discord.env.example` deleted; `hub.env.example` retained.
- D5: File-based credentials via Podman secrets (`type=mount`).

**Code check:**
- D1: `deploy/quadlet/lyra-data.volume` — confirmed exists. Not directly read here but
  `lyra-hub.container:35` references `Volume=lyra-data.volume:/home/lyra/.lyra:z`.
- D2: `deploy/quadlet/lyra-hub.container:22` → `UserNS=keep-id:uid=1500,gid=1500`.
  `lyra-telegram.container:23` → same. `lyra-discord.container:23` → same. Confirmed.
- D3: Hub mount visible at `:z` (not `:ro,z`). Confirmed.
- D4: `deploy/quadlet/` has `hub.env.example` but no `telegram.env.example` or
  `discord.env.example`. Only `clipool.env.example` is an additional env file.
- D5: `lyra-hub.container:33` → `Secret=lyra-nkey-hub,type=mount,target=hub.seed,...`. Confirmed.

**Note:** `UserNS=keep-id` in live code reads `UserNS=keep-id:uid=1500,gid=1500` — a refined form
vs the ADR's plain `UserNS=keep-id`. The ADR's intent is preserved (host-UID mapping), the
implementation detail evolved slightly. Not a conflict.

**T1 audit said:** MERGE-INTO ADR-055. All confirmed accurate.

**Decision: MERGE-INTO ADR-055** — content is accurate; merge as "Quadlet UID and Credential
Decisions" subsection.

---

### ADR-055 — Quadlet Ecosystem Conventions (Canonical)

**Body status:** Accepted. "Parent ADR: ADR-053."

**Normative claims (7 decisions):**
- D1: Image registry `localhost/<project>-<service>:latest`. `.github/workflows/publish.yml:13`
  → `ghcr.io/roxabi/lyra` (GHCR for CI/prod; `localhost/` for dev). The ADR's D1 is about
  per-project naming, not mandating localhost; GHCR images follow `ghcr.io/roxabi/<project>`.
  The ADR pre-dates GHCR publishing. Minor mismatch: ADR says `localhost/<project>-<service>`;
  live is `ghcr.io/roxabi/lyra` for prod images. The principle (project-named, no `roxabi-` prefix)
  holds.
- D2: Per-project NATS during migration; Phase 4 consolidation. `lyra-nats.container` is live on
  `roxabi.network`. `lyra-nats.container` notes: "Sole NATS on the host — host nats.service is
  retired (big-bang consolidation)" — this is the Phase 4 endpoint.
- D3: `roxabi.network` is live (`deploy/quadlet/roxabi.network` exists; `lyra-hub.container:15`
  → `Network=roxabi.network`). Phase 4 network consolidation has happened.
- D4: `~/.lyra/env/hub.env` — `lyra-hub.container:26` → `EnvironmentFile=%h/.lyra/env/hub.env`.
  Confirmed.
- D5: `lyra/scripts/deploy-lib.sh` — **does not exist**. D5 is noted as "Superseded
  (2026-05-03, #1035)". Current deploy path is `podman auto-update.timer`
  (`deploy/provision.sh:245-256`). D5 is explicitly superseded in the ADR body.
- D6: Independent release with batch only for shared-infra. No contradictory evidence.
- D7: Lyra repo as home for shared infra. Confirmed (infra ADRs live here, `deploy/quadlet/` has
  `roxabi.network`).

**Decision: KEEP-LIVE-ACCURATE** — canonical target, absorbs ADR-053, ADR-054, ADR-056 (and
optionally ADR-068). D5 supersession is already noted inline.

---

### ADR-056 — Container Publishing Workflow Pattern

**Body status:** Accepted. "Parent ADR: ADR-055."

**Normative claims:** Reusable GHA workflow (`Roxabi/.github/.github/workflows/publish-container.yml`);
7 fixes: semver strip-prefix, SHA-pinned actions, split metadata/push steps, drop `secrets:
inherit`, annotated tag for `@v1`, separate `LYRA_IMAGE`/`GHCR_IMAGE`, optional
`release_please_component`.

**Code check:**
- `.github/workflows/publish.yml:11` → `uses: Roxabi/.github/.github/workflows/publish-container.yml@v1`.
  The reusable workflow reference is live.
- `publish.yml` does not use `secrets: inherit` — confirmed (full content read).
- `release_please_component: lyra` is present (Finding 7 resolved: made optional with default "").
- Tag format: `tags: ['lyra/v*']` at `publish.yml:5` — component-prefix format retained.
- SHA pinning is on the reusable workflow side (in `Roxabi/.github`), not in `publish.yml` itself.

**T1 audit said:** MERGE-INTO ADR-055. Content is accurate and confirmed live.

**Decision: MERGE-INTO ADR-055** — content accurate; seven finding resolutions can be preserved
as "Container Publishing Conventions" subsection.

---

### ADR-057 — Security Event Audit Infrastructure

**Body status:** Accepted.

**Normative claims:**
- `AuditSink` Protocol in `lyra.core.cli.audit_sink`.
- `JetStreamAuditSink` in `lyra.infrastructure.audit`.
- `SecurityEvent` Pydantic model in `roxabi_contracts.audit`.
- `CliPool` accepts `audit_sink: AuditSink | None`.
- Fire-and-forget via `asyncio.create_task()`.
- Stream `LYRA_AUDIT`, subject prefix `lyra.audit.security`.

**Code check:**
- `src/lyra/core/cli/audit_sink.py:1,10` — `AuditSink(Protocol)` exists. Confirmed.
- `src/lyra/infrastructure/audit/__init__.py:3` → imports `JetStreamAuditSink`. Confirmed.
- `src/lyra/infrastructure/audit/jetstream_sink.py:19` → `_SUBJECT_PREFIX = "lyra.audit.security"`.
  Confirmed.
- `packages/roxabi-contracts/src/roxabi_contracts/__init__.py:12` → `from .audit import SecurityEvent`.
  Confirmed.
- `src/lyra/core/cli/cli_pool.py:80` → `audit_sink: AuditSink | None = None`. Confirmed.
- `src/lyra/bootstrap/factory/wiring_helpers.py:309` → `audit_sink = JetStreamAuditSink()` — wired
  in unified bootstrap. This resolves the ADR's "known gap in issue #855" for unified bootstrap.
- `src/lyra/bootstrap/standalone/hub_standalone.py:166` → also wires `JetStreamAuditSink`.

**ADR says:** "Unified bootstrap (`lyra start`) does not yet emit audit events — gap until #855."
**Code says:** `wiring_helpers.py:309` wires the sink in the unified factory. Gap appears resolved.
This is an accuracy issue: ADR-057 lists a Negative consequence that may no longer be true.

**Decision: KEEP-LIVE-NEEDS-EDIT** — all normative claims confirmed in code. The Negative
consequence about unified bootstrap gap may be stale (issue #855 likely resolved given
`wiring_helpers.py` wiring). Update: remove or qualify the "Unified bootstrap does not yet emit
audit events" negative consequence.

---

### ADR-067 — BlobStore Abstraction, Flat-FS Content-Addressed

**Body status:** Accepted.

**Normative claims:** New `packages/roxabi-blobs/` workspace member; `BlobStore` Protocol with
`put`/`get`/`exists`; `BlobRef` in `roxabi-contracts`; content-addressed flat-FS + SQLite;
deprecation of `audio_b64`/`audio_bytes` from `roxabi-contracts`.

**Code check:**
- `packages/` contains only `roxabi-contracts/` and `roxabi-nats/`. `roxabi-blobs/` does NOT exist.
- `grep -rn "BlobStore|blob_store|roxabi-blobs" src/ --include="*.py"` returns zero hits.
- No `BlobRef` in `packages/roxabi-contracts/`.

**Assessment:** ADR-067 is "Accepted" but `packages/roxabi-blobs/` has not been created. This is an
**accepted-but-not-yet-implemented** ADR — it records an architectural decision that has not landed
in code. It is not wrong; it is forward-looking.

**T1 audit said:** "Recommend Live standalone ADR-067." Confirmed — this is standalone and its
status is correctly Accepted (design decision made) even though implementation is pending.

**Falsification check:** Does the absence of the package mean the ADR should be archived? No —
ADRs can be accepted before implementation. The decision (what to build) is made. The architecture
is correct for the Lyra domain. Archiving it would lose the design intent.

**Decision: KEEP-LIVE-ACCURATE** — Proposed-without-code is not a reason to archive an Accepted
ADR. The design intent is sound and the package will be created when issue #1061 is implemented.
No edit needed. (Add a note "Implementation pending — `packages/roxabi-blobs/` not yet created"
if desired for clarity, but this is optional.)

---

### ADR-068 — SELinux :z Label Policy for Quadlet Bind-Mount Volumes

**Body status:** Accepted.

**Normative claims:**
- Keep `:z` on JetStream volume mount in `lyra-nats.container`.
- Add AppArmor-only LSM comment to `lyra-jetstream.volume`.
- Follow-up issue for Option (a): move to `~/.lyra/nats/jetstream/`.

**Code check:**
- `deploy/quadlet/lyra-nats.container:24` → `Volume=lyra-jetstream.volume:/var/lib/nats/jetstream:z`.
  The `:z` directive is present. Confirmed.
- `deploy/quadlet/lyra-jetstream.volume` — **does NOT contain the AppArmor-only LSM comment**
  the ADR prescribes for the PR #1062 implementation. Instead, the volume file shows the path has
  already been moved to `%h/.lyra/nats/jetstream` (the Option (a) follow-up has landed). The
  ADR-068 body-prescribed comment ("LSM note: this host runs AppArmor...") is absent — replaced
  by the structural fix that makes the comment obsolete.
- The ADR-068 body says: "open a follow-up issue for option (a)... the follow-up is not blocked on
  any other work." The `lyra-jetstream.volume:12` line (`Device=%h/.lyra/nats/jetstream`) shows the
  Option (a) structural fix has been applied. The comment added by PR #1062 was removed (as the
  ADR's follow-up scope required: "Remove the LSM comment added by PR #1062 — overlap is
  resolved, comment is obsolete").

**Assessment:** The ADR's two-phase plan (b in PR #1062, then a as follow-up) was executed. The
follow-up issue's migration has been applied. The `:z` directive remains (as intended — `:z` is
still correct on AppArmor, and the semantic reason for having it is documented in the ADR). The
structural overlap that the comment warned about is resolved.

**T1 audit said:** MERGE-INTO ADR-055 (judgment call). The code evidence now shows the ADR's
full lifecycle has completed (both phases executed). The narrow operational decision is fully
resolved. Merging into ADR-055 loses nothing the code needs.

**ADR-068 verdict: The follow-up structural fix (Option a) has landed. The ADR's full intent is
implemented. Merging into ADR-055 as a "Volume Security Labels" subsection is correct.**

**Decision: MERGE-INTO ADR-055** — both phases executed; narrow decision fully resolved; merge
under "Volume Security Labels" subsection of ADR-055.

---

### ADR-069 — provision.sh warn_subid_overlap Defensive Posture

**Body status:** Accepted.

**Normative claims:**
- F2 fix: separate missing/unreadable checks in `warn_subid_overlap`.
- F5 fix: whitelist guard `[[ "$file" == /etc/subuid || "$file" == /etc/subgid ]]`.
- F6 fix: blank line between functions.

**Code check:**
- `deploy/provision.sh:128-147` — `warn_subid_overlap` function.
- Line 131: `[[ "$file" == /etc/subuid || "$file" == /etc/subgid ]] || { warn "..."; return 0; }` —
  F5 whitelist guard present.
- Line 133: `[[ ! -e "$file" ]] && return 0` — missing → silent.
- Line 134: `[[ ! -r "$file" ]] && { warn "Cannot read $file (check permissions)..." ; return 0; }` —
  unreadable → warn. F2 fix present.
- Line 148: blank line before `assert_no_subid_overlap`. F6 fix present.

All three fixes confirmed in live code at exact lines described.

**Decision: KEEP-LIVE-ACCURATE** — all normative claims verified in code.

---

### ADR-070 — RenderEvent v2 AG-UI Modeling

**Body status:** Accepted.

**Normative claims:**
- Option B: extend internal `RenderEvent` hierarchy selectively with 4 event families.
- New events: `RunStartedRenderEvent`, `RunFinishedRenderEvent`, `RunErrorRenderEvent` (Slice 1);
  `ToolCallStartRenderEvent`, `ToolCallArgsRenderEvent`, `ToolCallEndRenderEvent`,
  `ToolCallResultRenderEvent` (Slice 3).
- `TextStartRenderEvent`, `TextDeltaRenderEvent`, `TextEndRenderEvent`, `TextChunkRenderEvent`
  (Slice 2 — text triplet).
- `ReasoningStartRenderEvent`, `ReasoningDeltaRenderEvent`, `ReasoningEndRenderEvent` (Slice 4).
- Each new event has `SCHEMA_VERSION_*` constant.
- `lyra-clipool` excluded from RenderEvent co-deploy gate.

**Code check:**
- `src/lyra/core/messaging/render_events.py:114-213` — Run lifecycle (S1) and ToolCall split (S3)
  events are present with SCHEMA_VERSION constants.
- S2 (text triplet): `TextStartRenderEvent`, `TextDeltaRenderEvent` NOT found in
  `render_events.py`. The comment at `_shared_streaming_emitter.py:133` says "#1099 must extend
  this branch when TextDeltaRenderEvent lands" — Slice 2 has NOT yet landed.
- S4 (reasoning): `ReasoningStartRenderEvent` NOT found in `render_events.py` — Slice 4 has NOT
  yet landed.
- `src/lyra/adapters/clipool/clipool_worker.py:20` imports from `lyra.core.messaging.events`
  (LlmEvent types), not `render_events` — confirmed clipool is outside the RenderEvent co-deploy
  gate.
- ADR-070 §References correctly states: "ADR-032 … partially superseded by #666 for SDK-specific
  content; the hexagonal pipeline contract remains normative for the CLI driver path."
- ADR-070 §References correctly states: "ADR-028 §Edge-Case Contract 3 superseded by Slice 3."

**Implementation state:** Slices 1 and 3 are implemented. Slices 2 and 4 are pending (#1099,
#1101). The ADR describes the full 5-slice design; partial implementation is by design (each slice
is independently mergeable).

**Decision: KEEP-LIVE-ACCURATE** — ADR describes the planned 5-slice architecture correctly.
Partial implementation (S2, S4 pending) does not make the ADR inaccurate — it describes an
in-progress epic. The decision (Option B, selective extension) is confirmed in code.

---

### ADR-071 — CLI Pool Claude OAuth Token Mechanism

**Body status:** Accepted (2026-05-07).

**Normative claims:**
- `lyra-clipool.container` declares `Secret=lyra-claude-oauth,type=env,target=CLAUDE_CODE_OAUTH_TOKEN`.
- `ANTHROPIC_API_KEY` excluded from `_SAFE_ENV_KEYS` with load-bearing comment + unit test.
- `type=env` exception to ADR-054's `type=mount` pattern.
- Podman bug #28075 verified not present at Podman 5.7.0.

**Code check:**
- `deploy/quadlet/lyra-clipool.container:31` → `Secret=lyra-claude-oauth,type=env,target=CLAUDE_CODE_OAUTH_TOKEN`.
  Confirmed.
- The comment at `:30` confirms bootstrap procedure per ADR.
- `src/lyra/core/cli/cli_pool_worker.py` has `_SAFE_ENV_KEYS` (referenced in ADR) — load-bearing
  comment and unit test (`tests/core/test_cli_pool_process.py::TestCliPoolSpawnEnv::
  test_anthropic_api_key_not_forwarded`) confirmed via grep.

**Decision: KEEP-LIVE-ACCURATE** — all normative claims verified in code. Live standalone.

---

## Decision Tally

| Decision | ADRs |
|----------|------|
| FULL-ARCHIVE | 012, 016, 018, 033, 034, 041, 043 |
| KEEP-LIVE-NEEDS-EDIT | 028, 032, 057 |
| KEEP-LIVE-ACCURATE | 042, 055, 067, 069, 070, 071 |
| MERGE-INTO ADR-055 | 053, 054, 056, 068 |

Total: 20 ADRs validated.

---

## Cross-Cluster Handoffs

### ADR-006/058/066 error-handling triplet (Cluster B signal)

ADR-057 (cluster D) documents `SecurityEvent` in `roxabi_contracts.audit`. The `ContractEnvelope`
base class is the same base used by all NATS contracts (ADR-049 territory). ADR-066
(`WorkerError` envelope extension — Cluster B/NATS Contracts) and ADR-057 both extend
`roxabi-contracts`. No merge conflict, but cluster B should verify that `SecurityEvent` schema
uses the same `_version_check` discipline as ADR-049 requires. Confirmed: `SecurityEvent` at
`packages/roxabi-contracts/src/roxabi_contracts/audit/__init__.py:15` extends `ContractEnvelope`.

### ADR-070 extends ADR-032 (both Cluster D)

Intra-cluster: ADR-032 partial-supersession banner must reference ADR-070. ADR-028 partial-
supersession banner must reference ADR-070 Slice 3. Both edits are within cluster D scope.

### ADR-055 D5 supersession

ADR-055 Decision D5 (`lyra/scripts/deploy-lib.sh`) is marked superseded in the ADR body. The
canonical deploy path is `podman auto-update.timer` (confirmed in `deploy/provision.sh`). Wave 2
doc-writer working on ADR-055 enrichment should note this inline supersession clearly.

---

## Surprises and Contested Calls

### ADR-041 body is nearly empty — self-gutted

The ADR-041 file has had its content removed by PR #886/#1036 authors, leaving only a Status
note. The T1 audit was correct to flag it as archive, but the body state is unusual: it
effectively reads as already pre-archived in place. Wave 2 move to archive/ is a formality.

### ADR-053 historical content is load-bearing

ADR-053 contains the full ecosystem migration order table (lyra → voiceCLI → imageCLI → deferred
projects) and the rationale for keeping supervisord during the migration window. This context is
not duplicated in ADR-055. Wave 2 must include this historical context when merging ADR-053 → 055.

### ADR-055 D1 vs live GHCR naming

ADR-055 D1 specifies `localhost/<project>-<service>:latest` but live publish uses
`ghcr.io/roxabi/lyra` for CI/prod images. The principle (project-named, no `roxabi-` prefix for
container images) holds, but the concrete example (`localhost/`) predates the GHCR publish pattern
established by ADR-056. Wave 2 enrichment of ADR-055 should reconcile D1 to reflect GHCR as the
registry for CI/prod images and `localhost/` for local dev builds.

### ADR-068 follow-up already executed

The ADR-068 Option (a) follow-up (moving JetStream data to `~/.lyra/nats/jetstream/`) has already
landed in `lyra-jetstream.volume`. The LSM comment prescribed by PR #1062 scope has been removed
(as the ADR's follow-up instructions required). The ADR's two-phase lifecycle is complete. This
supports merging into ADR-055 rather than keeping standalone.

### ADR-067 is Accepted-without-code

`packages/roxabi-blobs/` does not exist. ADR-067 is Accepted (design decision made) for work not
yet implemented. Keeping it live is correct — it is a pending architectural decision, not stale
history. Wave 2 should NOT archive it.

### ADR-043 overlap with podman auto-update

ADR-043 (Proposed, never built) was superseded in practice by `podman auto-update.timer` + GHCR.
The M16 fix (fetch timeout returncode) is irrelevant since `deploy.sh` is retired. Archiving with
`superseded_by: ADR-055` is clean.

---

## Wave 2 Hand-off Table (Cluster D tasks)

| ADR | Action | Target | Notes |
|-----|--------|--------|-------|
| ADR-012 | Archive | — | `superseded_by: ADR-016` → wait, that's also archived. Use `superseded_by: historical — #666 removed AnthropicAgent` |
| ADR-016 | Archive | — | `superseded_by: historical — #666 removed AnthropicSdkDriver` |
| ADR-018 | Archive | — | `superseded_by: historical — #666 removed AnthropicSdkDriver` |
| ADR-028 | Add partial-supersession banner | — | "Edge-Case Contract 3 superseded by ADR-070 Slice 3 (#1100). Contracts 1, 2, 4 remain in force." |
| ADR-032 | Add partial-supersession banner | — | "SDK streaming content superseded by #666. Hexagonal pipeline contract (`LlmEvent → StreamProcessor → RenderEvent`) remains normative; extended by ADR-070." |
| ADR-033 | Archive | ADR-042 | `superseded_by: ADR-042` |
| ADR-034 | Archive | ADR-042 | Already self-declared; formalize |
| ADR-041 | Archive | ADR-047, ADR-053 | Self-declared; formalize |
| ADR-042 | Keep as-is | — | Canonical brand migration record |
| ADR-043 | Archive | ADR-055 | `superseded_by: ADR-055` (D5 deploy pattern) |
| ADR-053 | Merge → ADR-055 | ADR-055 | Include D1-D3 content + ecosystem migration order table |
| ADR-054 | Merge → ADR-055 | ADR-055 | UID + credential decisions subsection |
| ADR-055 | Keep + enrich | — | Absorb 053/054/056/068; reconcile D1 for GHCR |
| ADR-056 | Merge → ADR-055 | ADR-055 | Container publishing conventions subsection |
| ADR-057 | Keep + edit | — | Update unified-bootstrap gap note (issue #855 appears resolved) |
| ADR-067 | Keep as-is | — | Accepted, pending implementation (#1061) |
| ADR-068 | Merge → ADR-055 | ADR-055 | Option (a) already executed; merge under Volume Security subsection |
| ADR-069 | Keep as-is | — | All claims verified; standalone appropriate |
| ADR-070 | Keep as-is | — | Slices 1+3 live; 2+4 pending; ADR describes planned architecture correctly |
| ADR-071 | Keep as-is | — | All claims verified; standalone appropriate |
