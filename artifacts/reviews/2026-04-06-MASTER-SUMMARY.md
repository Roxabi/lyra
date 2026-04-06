# Lyra — 48-Hour Review Master Summary
_2026-04-06. Covers commits from the prior 48-hour sprint. Reference documents:_
_`2026-04-06-ARCHITECTURE-REFERENCE.md` · `2026-04-06-SECURITY-POSTURE.md`_

---

## Overall Health Verdict

**WARN — Solid foundation, targeted fixes needed.**

No critical bugs. No data loss paths. No crashes in the happy path. The hexagonal boundaries are clean, NATS reliability improvements are well-designed and well-tested, and the three-process production topology is stable. What remains is a collection of localized medium and low-severity issues — most are one- to three-line fixes.

The codebase is production-worthy at current single-hub scale. The issues compound under horizontal scaling and future schema bumps, which is why they need fixing before those milestones.

---

## What Was Built (32 Commits in 48 Hours)

Grouped by theme:

| Theme | Key Commits | Summary |
|-------|------------|---------|
| InboundMessage unification | `520c33b` (#534 Slice 1) | Unified `InboundAudio` into `InboundMessage` via `modality` + `AudioPayload`; added `SttMiddleware`; deleted `AudioPipeline.run()` dead path; compat shim for legacy audio subject |
| Wire format + schema versioning | `d970d3a`, `747211e` | Added `schema_version` field to all 5 envelope types; centralized constants; rate-limited mismatch logs; per-instance version-mismatch counters |
| NATS reliability | `1bc363e`, `453b8fa` | Queue group constants (`queue_groups.py`); supervisor priority ordering (hub=100, adapters=200); hub readiness probe (`lyra.system.ready` request-reply) |
| stream_error crash recovery | `4ec2417` | Hub publishes `stream_error` envelope on exception and graceful shutdown; adapter enqueues poison pill or tombstones; atomic swap in `publish_stream_errors` |
| Publish-only mode | `ec90576` (#541) | Adapter-side `NatsBus` uses `publish_only=True` — no phantom subscriptions; `_started` flag closes post-start registration bypass |
| Outbound listener protocol | `aa57879` (#550) | Extracted `OutboundListener` Protocol; adapters typed at protocol level; decoupling complete at type level |
| Voice NATS decoupling | `e0bd438` (#519) | STT/TTS moved from in-process to NATS request-reply microservices; hub is now voice-infrastructure-agnostic; `NatsSttClient`/`NatsTtsClient` implement protocols structurally |
| Security hardening | `8c19400`, `343ff69`, `1f0c911`, `4f5c884` | nkey auth wiring; platform_meta allowlist sanitization; session scope validation; cache bounding; TTL reaper; credential scrubbing; auth key injection guard; structured deserialization |
| Bootstrap refactor | `746b4af` | Replaced `_bootstrap_multibot` with `_bootstrap_unified`; embedded NATS auto-start for dev/single-machine mode; renamed bootstrap files |
| Deploy fix | `817fd9e` | SHA cache for broken staging deploy loop — `FAIL_FILE` prevents infinite retry on same bad SHA |

---

## Verdicts by Domain

| Domain | Verdict | Key Concerns |
|--------|---------|-------------|
| Architecture / Hexagonal compliance | PASS | Clean boundaries; no anti-patterns in domain; pluggability well-designed |
| NATS wire format | WARN | OutboundMessage schema_version unchecked on receive; `_ENVELOPE_VERSIONS` KeyError deferred to handler time |
| NATS reliability | WARN | Non-deterministic tombstone eviction; queue-full poison-pill stall (120s); readiness responder missing audio bus count |
| Stream error / crash recovery | WARN | Queue-full poison-pill drop (bounded degradation, 2min stall); `set.pop()` eviction ordering |
| Voice decoupling | PASS | Clean separation; protocols correctly applied; graceful degradation present |
| Security | WARN | `scrub_nats_url()` not applied in `sys.exit()` paths (HIGH); NATS server auth not activated in prod (MEDIUM); TTS/STT bypass nkey |
| Bootstrap / deploy | WARN | `FAIL_FILE` comment misleading; never pruned on success; `_acquire_lockfile()` outside try/finally (theoretical) |
| DevOps / supervisor | WARN | Two supervisor conf.d trees (currently in sync but divergence risk); STT/TTS priority not explicit |

---

## All Issues Ranked

### HIGH

| Issue | File:line | Fix |
|-------|----------|-----|
| `scrub_nats_url()` not called in `sys.exit()` error paths — raw NATS URL with credentials reaches supervisord stderr | `adapter_standalone.py:46`, `hub_standalone.py:154` | Wrap both `sys.exit()` strings with `scrub_nats_url(nats_url)` — one line each, already imported |

### MEDIUM

| Issue | File:line | Fix |
|-------|----------|-----|
| NATS server-side nkey enforcement not activated in production — unauthenticated connections accepted from any localhost process | Deployment config (not source) | Switch from `nats-local.conf` to `nats.conf` in supervisor launch config |
| TTS and STT adapters bypass nkey auth — call `nats.connect()` directly | `tts_adapter_standalone.py:73`, `stt_adapter_standalone.py:44` | Replace with `nats_connect()`; add seeds to `gen-nkeys.sh` |
| `platform_meta` allowlisted values have no type or length validation | `src/lyra/nats/_sanitize.py:26-44` | Add type coercion + 256-char cap after key allowlisting |
| `assert hub._msg_manager is not None` in SttMiddleware crashes with AssertionError (not user-visible reply) | `src/lyra/core/hub/middleware_stt.py:91, 105` | Replace asserts with `if ... return _DROP` + ERROR log |
| Non-deterministic tombstone eviction in `remember_terminated` — `set.pop()` is arbitrary | `src/lyra/adapters/nats_stream_decoder.py:91` | Replace `set` with `collections.OrderedDict` for FIFO eviction |
| `OutboundMessage` deserialization skips `check_schema_version` — silent misinterpret on first schema bump | `src/lyra/adapters/nats_outbound_listener.py:144, 191` | Add `check_schema_version(outbound_data, SCHEMA_VERSION_OUTBOUND_MESSAGE)` before `_deserialize_dict` |
| `_ENVELOPE_VERSIONS` KeyError deferred to handler time — unregistered item_type causes silent bus shutdown | `src/lyra/nats/nats_bus.py:256` | Move `_ENVELOPE_VERSIONS[self._item_type]` lookup into `NatsBus.start()` |
| Readiness responder `buses` count missing `inbound_audio_bus` (cosmetic inconsistency) | `src/lyra/bootstrap/hub_standalone.py:435-437` | Either remove `buses` field or document intentional omission |
| Supervisor conf.d drift risk — two trees exist (`supervisor/conf.d/` and `deploy/supervisor/conf.d/`) | Both directories | Add CI diff check or consolidate to single tree |

### LOW

| Issue | File:line | Fix |
|-------|----------|-----|
| Queue-full poison-pill drop — drain loop stalls 120s when stream_error arrives on a full queue | `src/lyra/adapters/nats_stream_decoder.py:114-119` | Document 120s as recovery path; add test asserting bounded drain time |
| `FAIL_FILE` comment says "Cleared automatically" — false; file grows unbounded | `scripts/deploy.sh:36` | Fix comment; add `rm -f "$FAIL_FILE"` after successful deploy |
| `cast(InboundMessage, ...)` is unsound for `InboundAudio` cache entries | `src/lyra/adapters/nats_outbound_listener.py:148, 170, 261` | Track as Slice 2 (#534) cleanup blockers; add `# type: ignore` with issue reference |
| `_QUEUE_GROUP = "hub-inbound"` hardcoded in legacy compat handler | `nats/compat/inbound_audio_legacy.py:35` | Import `HUB_INBOUND` from `queue_groups.py` |
| `ToolSummaryRenderEvent` decoded without forwarding `schema_version` from payload | `src/lyra/nats/render_event_codec.py:98-109` | Forward `schema_version` or switch to `deserialize()` like `TextRenderEvent` |
| nkey seed file permission not validated — world-readable seed accepted silently | `src/lyra/nats/connect.py:29-33` | Add `stat()` check; warn/exit if `(mode & 0o777) != 0o600` |
| `_terminated_streams` set not cleaned by reaper — tombstoned IDs that never drain linger | `src/lyra/adapters/nats_outbound_listener.py:67` | Have reaper evict `_terminated_streams` entries |
| No key rotation tooling or expiry mechanism | `deploy/nats/gen-nkeys.sh` | Document rotation runbook; consider adding rotation steps |
| No STT/TTS nkeys — voice adapters share hub subject permissions | `deploy/nats/gen-nkeys.sh` | Generate per-service seeds (track as issue; not urgent for single-machine) |
| Discord `thread_sessions` eviction in `retrieve_thread_session` is silent | `src/lyra/adapters/discord_threads.py:128-131` | Add eviction log |
| `_TTS_CONFIG_FIELDS` duplicated in `nats_tts_client.py` and `tts_adapter_standalone.py` | Both files | Extract to shared constant (e.g., `src/lyra/tts/_fields.py`) |
| `NatsSttClient`/`NatsTtsClient` have no unit tests | `src/lyra/nats/nats_stt_client.py`, `nats_tts_client.py` | Add unit tests for timeout, MaxPayload, ok=False paths using mock NATS |
| `TtsUnavailableError` → text fallback path in `AudioPipeline` has no test | `tests/core/test_audio_pipeline_tts.py` | Add test for the failure recovery path |
| `remember_terminated` at `_MAX_TERMINATED_STREAMS` boundary untested | (no test file) | Add test asserting FIFO eviction order once data structure is fixed |
| `_acquire_lockfile()` called outside `try/finally` in `unified.py` | `src/lyra/bootstrap/unified.py:56-57` | Move inside `try` block or add explicit guard (theoretical leak) |
| STT/TTS supervisor programs have no explicit priority | `deploy/supervisor/conf.d/lyra_stt.conf:6` | Add `priority=300` to make intent explicit |
| Double sleep on `NoRespondersError` path in `wait_for_hub` — 1.0s per attempt instead of 0.5s | `src/lyra/nats/readiness.py:100-101` | Track elapsed time inside `nc.request()` and deduct from sleep |

---

## Action Roadmap

### This Week

| Priority | Action | Location |
|----------|--------|---------|
| P0 | Apply `scrub_nats_url()` to both `sys.exit()` failure strings | `adapter_standalone.py:46`, `hub_standalone.py:154` |
| P1 | Activate NATS server-side nkey enforcement in production | Deployment config |
| P1 | Migrate TTS and STT adapters to `nats_connect()` | `tts_adapter_standalone.py:73`, `stt_adapter_standalone.py:44` |
| P1 | Replace `assert hub._msg_manager` with graceful drop in `SttMiddleware` | `middleware_stt.py:91, 105` |
| P1 | Fix `FAIL_FILE` comment and add `rm -f "$FAIL_FILE"` on successful deploy | `scripts/deploy.sh:36` |
| P2 | Add `check_schema_version` for `OutboundMessage` in `NatsOutboundListener` | `nats_outbound_listener.py:130-194` |
| P2 | Move `_ENVELOPE_VERSIONS` lookup into `NatsBus.start()` | `nats_bus.py:138-154` |
| P2 | Replace `set` with `OrderedDict` for `_terminated_streams` eviction | `nats_stream_decoder.py:88-92` |

### This Month

| Priority | Action | Location |
|----------|--------|---------|
| P3 | Add value-shape validation in `sanitize_platform_meta()` | `src/lyra/nats/_sanitize.py:26-44` |
| P3 | Add CI diff check for supervisor conf.d trees | CI config |
| P3 | Add unit tests for `NatsSttClient` and `NatsTtsClient` | New test files |
| P3 | Add test for `TtsUnavailableError` → text fallback | `test_audio_pipeline_tts.py` |
| P3 | Add test for queue-full poison-pill path in `handle_stream_error` | `test_nats_stream_decoder.py` |
| P3 | Import `HUB_INBOUND` from `queue_groups.py` in `inbound_audio_legacy.py` | `nats/compat/inbound_audio_legacy.py:35` |
| P3 | Fix `ToolSummaryRenderEvent` decode to forward `schema_version` | `nats/render_event_codec.py:98-109` |

### Backlog

| Priority | Action | Location |
|----------|--------|---------|
| P4 | Execute #534 Slice 2 — delete `nats/compat/`, retire `InboundAudio`, retire legacy audio subject | Multiple files |
| P4 | Add `MemoryPort` Protocol (if second memory backend is ever introduced) | `src/lyra/core/memory.py` |
| P4 | Replace if/elif in `_create_agent()` with registry lookup | `bootstrap/agent_factory.py:151-206` |
| P4 | Have reaper clean `_terminated_streams` | `nats_outbound_listener.py` |
| P4 | Document nkey seed rotation runbook | `deploy/nats/` or `docs/DEPLOYMENT.md` |
| P4 | Generate per-service STT/TTS nkeys | `deploy/nats/gen-nkeys.sh` |
| P4 | Document readiness probe in ops runbook | `docs/ARCHITECTURE.md` or `docs/OPERATIONS.md` |
| P4 | Validate nkey seed file permissions in `_read_nkey_seed()` | `src/lyra/nats/connect.py:29-33` |
| P4 | Extract `_TTS_CONFIG_FIELDS` to shared location | `src/lyra/tts/_fields.py` (new) |
| P4 | Add explicit priority to STT/TTS supervisor configs | `deploy/supervisor/conf.d/lyra_stt.conf` |

---

## What's Solid — Don't Touch

| Component | Why it's solid |
|-----------|---------------|
| Hexagonal boundary enforcement | Zero infra imports in `core/`; verified by grep; no anti-patterns found |
| `ChannelAdapter` Protocol | Correctly placed port; adapters implement structurally; hub never holds concrete ref |
| `LlmProvider` Protocol + ProviderRegistry | Fully pluggable; decorator stack at bootstrap; no hub coupling |
| Queue group architecture | `queue_groups.py` single source of truth; correctly applied at all 4 subscription sites |
| stream_error atomic swap | `publish_stream_errors` atomic swap eliminates race between shutdown and in-flight `finally` |
| Publish-only mode | `_started` flag closes the registration bypass; all 4 adapter-side bus sites use `publish_only=True` |
| schema_version check rules | Forward-compat, backward-compat, bool exclusion, rate-limited logs — all correct |
| Cache bounding + TTL reaper | All 5 caches now bounded; reaper uses snapshot iteration; double-pop safe |
| Auth key injection guard | `_RESERVED_AUTH_KEYS` guard in `nats_connect(**extra)` prevents accidental bypass |
| `platform_meta` allowlist boundary | Applied at NATS receive boundary before staging queue; correct placement |
| Session scope validation | `thread_session_id` validated against `pool_id` ownership — cross-pool hijack closed |
| Supervisor startup ordering | hub priority=100, adapters priority=200; NATS readiness probe correctly placed after all subscriptions |
| Deploy SHA cache | `FAIL_FILE` pattern correctly prevents infinite retry on same bad SHA |
| Test suite | 2492 tests pass, 81.67% coverage; real NATS integration tests for queue group distribution |

---

## Foundation Assessment

**Is this a solid base to build on?** Yes.

The core architecture is sound. Hexagonal boundaries are clean and consistently enforced. The NATS transport layer is mature enough for production use at single-hub scale. The security posture has been meaningfully hardened in this sprint.

**Main extension vectors:**

| Vector | Readiness |
|--------|---------|
| New LLM backend | High — implement `LlmProvider`, register in `ProviderRegistry`, add config |
| New platform (WhatsApp, Slack) | Medium — implement `ChannelAdapter`, wire in bootstrap, add to `Platform` enum |
| New voice engine | High — implement request-reply service on `lyra.voice.*.request` subjects; zero Lyra code changes |
| New pipeline middleware | High — implement `PipelineMiddleware`, insert in `build_default_pipeline()` |
| New slash command | High — `plugin.toml` + `handlers.py`; auto-discovered |

**What to do next (in order):**

1. Fix the HIGH security bug (`scrub_nats_url` in `sys.exit()` paths) — this week.
2. Activate NATS server-side nkey enforcement in production — this week.
3. Fix the MEDIUM bugs (`assert` → graceful drop in `SttMiddleware`; TTS/STT nkey bypass) — this week.
4. Execute #534 Slice 2 (retire legacy audio subject, delete compat shim) — before adding a third platform adapter.
5. Add `check_schema_version` for `OutboundMessage` — before any `OutboundMessage` schema bump.
6. Replace `set.pop()` with `OrderedDict` in `remember_terminated` — before enabling concurrent streams at scale.
