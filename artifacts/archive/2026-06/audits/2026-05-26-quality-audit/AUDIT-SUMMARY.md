# Code Quality Audit Summary — Lyra Stage-Axis Post-Refactor

**Date:** 2026-05-27
**Scope:** 81 domain reports across 8 quality dimensions (10 architecture, 10 security, 16 code-smells, 10 async-patterns, 10 error-handling, 9 type-safety, 6 test-quality, 10 tech-debt)
**Baseline:** Prior audit 2026-05-18 (hexagonal / mutualization / simplification)
**Context:** Epic #1277 stage-axis refactor active

---

## Fix Log (post-audit)

| When | PR | Fixes |
|------|-----|-------|
| 2026-05-27 | #1431 | P0 — refine CLI blocking event loop (#1424) |
| 2026-05-27 | #1432 | P0 — runtime_config.set_param cognitive hotspot (#1425) |
| 2026-05-27 | #1443 | P1 — CliLlmProvider.chat() sync subprocess in async context |
| 2026-05-27 | #1444 | P1 — Bootstrap adapter cleanup + teardown protection |
| 2026-05-27 | #1445 | P1 — Admin path traversal in /folder and /workspace |
| 2026-05-27 | #1446 | P1 — SubmitToPoolMiddleware broad `except Exception` narrowed |

*Remaining P1 count: 106 → 101 (2 P0 + 4 P1 resolved).*

## Issue Tracking (post-audit)

Top 5 remaining P1 findings filed as child issues of #1426:

| Issue | Domain | Title | Size | Priority |
|-------|--------|-------|------|----------|
| #1447 | Security | Audio attachment path traversal validation | S | High |
| #1448 | Error Handling | Guard `pool.submit()` in `_dispatch_pipeline_result` | S | High |
| #1449 | Code Smells | Extract retry/kind-router/post-send from `dispatch_outbound_item` | L | High |
| #1450 | Architecture | Close ADR-048 gap (`ResumePublisherPort` + halt concrete setters) | L | High |
| #1451 | Test Quality | Backfill bootstrap wiring tests | L | High |

## Executive Summary

- **Overall health is mixed but not alarming:** 1,011 findings across ~917 unique source + test files, yet only 2 Critical and 106 High. The long tail is Low/Medium (88 %), indicating many small cleanups rather than systemic failure. Stage-axis decomposition is structurally sound; the dominant architectural gap is the unfinished ADR-048 port migration (direct infrastructure imports in `core/hub` and `core/nonhub`).
- **Security posture is solid at the core, soft at the edges:** No critical security vulnerabilities in production runtime code. The single High finding is a path-traversal vector in audio-attachment handling (`P08`). Most residual risk lives in configuration-time env-var paths and NATS ACL over-permission (#1293).
- **Test coverage is polarized:** `src/lyra/core` achieves 88 % line coverage, but bootstrap wiring modules sit at 0–24 % and 48+ `asyncio.sleep` calls create endemic flaky timing. Parametrization is effectively unused (2 usages across ~1,655 core tests).
- **Key debt items:** ADR-048 protocol coverage stalled at ~44 % in the hub; 140 tech-debt annotations (zero TODO/FIXME/HACK/XXX — good hygiene); two stale closed-epic phase references (#753); one live transitional placeholder (`b""` in STT, tracked by #1067); both P0 critical issues fixed on 2026-05-27 (#1424 refine blocking loop, #1425 runtime_config hotspot).

---

## Domain Metrics Dashboard

| Domain | Files | Findings | Critical | High | Medium | Low |
|--------|-------|----------|----------|------|--------|-----|
| Architecture | 585 | 67 | 0 | 11 | 25 | 30 |
| Async Patterns | 245 | 85 | 1 | 8 | 33 | 43 |
| Code Smells | 366 | 242 | 1 | 43 | 95 | 101 |
| Error Handling | 310 | 98 | 0 | 16 | 35 | 47 |
| Security | 229 | 73 | 0 | 1 | 27 | 43 |
| Tech Debt | 403 | 140 | 0 | 4 | 45 | 91 |
| Test Quality | 204 | 125 | 0 | 21 | 43 | 54 |
| Type Safety | 254 | 181 | 0 | 2 | 43 | 133 |

*Note: Domain scopes overlap; the same source and test files are audited across multiple dimensions. Unique codebase ≈ 485 source + 432 test files.*

---

## Severity Distribution

| Severity | Count | % |
|----------|-------|---|
| Critical (P0) | 2 | 0.2 % |
| High (P1) | 106 | 10.5 % |
| Medium (P2) | 346 | 34.2 % |
| Low (P3) | 542 | 53.6 % |
| Info | 15 | 1.5 % |
| **Total** | **1,011** | **100 %** |

---

## Critical Issues (P0)

*Status: 2/2 fixed as of 2026-05-27*

1. **[FIXED #1431] Blocking event-loop bug in `lyra agent refine` (`async-patterns/P08`)**
   `AgentRefiner.run_session(io)` is a synchronous method that calls `input()` and `subprocess.run(["claude", "--print", ...])` inside an async CLI coroutine. This freezes the asyncio event loop for the full duration of an interactive refinement session and will crash with `RuntimeError` if invoked from an existing loop (the normal Lyra runtime).
   *File:* `src/lyra/agent_cmd/agents/edit_cmd.py:264`

2. **[FIXED #1432] Cognitive-complexity critical hotspot in runtime config (`code-smells/P04`)**
   `set_param` in `runtime_config.py` is 124 lines with estimated cognitive complexity ~124. The monolithic `if/elif` chain handles every config key with validation, coercion, and side effects in one function, making the config subsystem extremely brittle to refactor or extend.
   *File:* `src/lyra/core/runtime_config.py:176`

---

## High Priority (P1)

### Architecture
- `MessagePrepMiddleware` (stage 7) drills into `ctx.hub._turn_publisher` and `ctx.hub._turn_store` to build a JetStream resume closure — direct transport/infrastructure coupling with no core protocol. → **#1450**
- Hub registration API (`set_turn_publisher`, `set_typing_publisher`) accepts 5 concrete store/transport types lacking `core/ports/` protocols, expanding the ADR-048 gap. → **#1450**
- `lyra.inbound` is absent from all 7 `.importlinter` contracts; bidirectional `adapters ↔ inbound` coupling is invisible to CI.
- `LlmClient` bypasses `WorkerPoolClient` circuit breaker / routing to call `_transport.call()` directly for `reset`, `resume_and_reset`, `switch_cwd`. → **#1450**

### Async Patterns
- `OutboundEmitter.run()` abandons the `events` async iterator on the first `__anext__()` exception without `aclose()`, leaking NATS queue consumers.
- **[FIXED #1443]** `CliLlmProvider.chat()` uses sync `subprocess.run(...)` to shell out to `claude --print` from an async context.
- **[FIXED #1431]** `AgentRefiner.apply_patch()` nests `asyncio.run(_apply())`, which will raise `RuntimeError` inside the normal Lyra event loop.
- **[FIXED #1444]** Bootstrap standalone adapters do not clean up previously-wired buses, NATS subscriptions, or stores if `adapter.astart()` raises inside the per-bot loop.

### Code Smells
- `dispatch_outbound_item` is 167 LOC with explicit `noqa: C901, PLR0913, PLR0915`; handles 6 message kinds, retry loops, circuit breaker, and iterator draining in one function. → **#1449**
- God classes persist: `Hub` (10+ subsystems), `OutboundRouter` (6 dispatch types), `PoolManager` (6 lifecycle responsibilities), `OutboundEmitter` (9+ responsibilities).
- `packages/roxabi-blobs/fs_store.py` exceeds the 300-line cap without exemption (344 LOC).
- Fake worker test doubles in `roxabi-nats.testing` are ~80 % copy-paste across `voice.py` and `image.py`.

### Error Handling
- `pool.submit()` in `_dispatch_pipeline_result` is unguarded; a synchronous exception crashes the `Hub.run()` consumer loop. → **#1448**
- `ThreadStore.update_session` failures are silently swallowed inside `_thread_update_fn`, causing data loss (new session_id never persisted).
- **[FIXED #1446]** Broad `except Exception` in `SubmitToPoolMiddleware` swallows store outages as `ResumeStatus.SKIPPED`, masking infrastructure failures.
- **[FIXED #1444]** Bootstrap teardown sequences (`teardown_buses`, `teardown_dispatchers`) are unguarded; one failure orphans remaining resources.

### Security
- Audio-attachment path traversal: `Path(str(audio_attachment.url_or_path_or_bytes))` is unvalidated, enabling arbitrary file read/delete via `read_bytes()` + `unlink()`. → **#1447**
- Telegram token regex in `trace.py` is too narrow (`-`, `_` only), allowing partial token leakage into logs.
- **[FIXED #1445]** Admin `/folder` and `/workspace` commands resolve arbitrary paths without a base-directory constraint.

### Test Quality
- `test_hub_tts_dispatch.py` mocks SUT methods (`hub.dispatch_audio`, `hub.dispatch_response`) instead of asserting through injected adapter fakes.
- `test_outbound_dispatcher_coverage.py` patches `asyncio.sleep` globally and patches `try_notify_user` to speed up backoff tests.
- Bootstrap wiring modules have severe coverage gaps: `agent_store_factory` 0 %, `bot_agent_map` 12 %, `unified` 24 %, `wiring_helpers` 32 %. → **#1451**

### Type Safety
- `factory/wiring_helpers.py` has a file-level `# pyright: reportAttributeAccessIssue=false, reportArgumentType=false` suppression covering the entire module.
- `roxabi_nats/_serialize.py` uses 34 `typing.Any` annotations (59 % of the package total) in its recursive encode/decode engine.

---

## Medium Priority (P2)

### Architecture
- `PoolManager` passes the full `Hub` concrete class instead of the documented `PoolContext` protocol.
- Transitive dependency chain: `ports/llm.py` → `agent_config.py` → `command_router.py` → `pool.py` couples the LLM port to the session stage.
- Cross-package bidirectional cycle: `roxabi-contracts` (schema layer) imports backward-compat shims from `roxabi-nats` (transport SDK).

### Async Patterns
- `PoolManager` uses `threading.Lock()` in an asyncio context; safe today (no `await` inside lock) but blocks the event loop if future edits add async I/O.
- Race window in `OutboundDispatcher._worker_loop` between `create_task` and `self._scope_tasks.add(task)` leaks tasks during `stop()`.
- `BaseException` handler in `dispatch_outbound_item` re-raises `CancelledError` without draining streaming iterators, leaving async generators open.

### Code Smells
- `SttMiddleware.__call__` is 96 LOC with 6 inline outcome branches (explicit `noqa: C901, PLR0915`).
- `StreamingDispatch.dispatch` is 90 LOC, mixing voice-tee setup, fallback routing, and text accumulation.
- Duplicate TTS synthesis block repeated verbatim in 3 methods of `TtsDispatch`.

### Error Handling
- `Hub.run()` swallows all pipeline stage errors with `except Exception`, silently dropping messages without user feedback or `MessageDropped` events.
- 16 broad `except Exception` / `except BaseException` blocks (32–43 % of catches per partition); 3 sit on paths that silently degrade UX.
- Zero `raise ... from e` exception chaining across the entire codebase; original tracebacks are systematically discarded at translation boundaries.

### Security
- NATS subject injection via unsanitized f-string interpolation in `TypingPublisher` (`lyra.typing.{platform}.{bot_id}`) enables wildcard-driven expansion.
- PII exposure: `AuditConsumer` logs `user_id`, `scope_id`, and `platform` at INFO level as structured JSON with no redaction.
- Broad NATS ACL grants (`$JS.API.>`, `$KV.lyra-state.>`) in production `auth.conf` violate least-privilege (tracked #1293).

### Test Quality
- 48+ `asyncio.sleep` calls across 19 core test files; 7 files have > 5 sleeps each.
- Only 2 `@pytest.mark.parametrize` usages across ~1,655 core test functions (0.12 %).
- 153 test names (~9 %) do not follow `test_<unit>_<condition>_<expected>` convention.

### Type Safety
- `CommandRouter` is typed as `Any` in `PipelineContext` and `MessagePrepMiddleware`, bleeding unchecked types across the inbound pipeline.
- TTS parameters use `object | None` duck-typing to avoid circular imports instead of a `TYPE_CHECKING` Protocol.
- `_ITEM = tuple` in `outbound_errors.py` is an unparameterised alias for 6 heterogeneous queue shapes.

### Tech Debt
- 5 infrastructure store classes (`TurnStore`, `MessageIndex`, `PrefsStore`, `TurnPublisher`, `TypingPublisher`) are imported directly into `core/hub` with no matching protocols.
- 13 `DEBT:complexity-residual` / `DEBT:boundary-broad-catch` / `DEBT:re-export-init` annotations remain in hot paths.
- Two stale `#753 Phase 3` comments persist in `hub_dispatch.py` and `outbound_router.py` despite the epic being closed.

---

## Low Priority (P3)

- Minor import-time cycle workarounds (deferred imports of `RoutingKey` in `middleware_guards.py`).
- Transitional `b""` placeholder in `SttMiddleware` pending BlobRef resolution (#1067 / V8).
- Magic literal debt: backoff tuple `(1.0, 2.0, 4.0)`, hardcoded `30000` ms STT timeout, magic divisor `10` in eviction throttle.
- Folder exemption overage in `src/lyra/core` (14 vs limit 12, tracked #858 / #1020).
- Bare `repr(exc)` leak at `outbound/emitter.py:465` bypassing the `SanitizedError` boundary.
- 9 % of core test names violate the naming convention; 719 `MagicMock` / `AsyncMock` instantiations across 130 core test files.
- `obs/` scaffolding (3 files, 0 runtime consumers) adds surface area with no integration timeline (#1235).
- `lyra.monitoring` and `SupervisorctlManager` are deprecated but still in tree, blocked on #1035 with no visible milestone.

---

## Cross-Domain Hotspots

Files with findings in ≥3 domains:

| File | Domains | Findings |
|------|---------|----------|
| `src/lyra/outbound/emitter.py` | 6 (arch, async, smells, errors, debt, types) | 24 |
| `src/lyra/llm/llm_client.py` | 6 (arch, async, smells, errors, security, types) | 13 |
| `session_builder.py` | 5 (arch, async, smells, errors, debt) | 16 |
| `src/lyra/blobstore/_handlers.py` | 5 (arch, smells, errors, security, types) | 11 |
| `src/lyra/agents/simple_agent.py` | 5 (async, smells, errors, security, types) | 11 |
| `src/lyra/tools/gh_token/helper.py` | 5 (smells, errors, security, debt, types) | 10 |
| `src/lyra/transport/worker_pool_client.py` | 5 (arch, smells, errors, tests, types) | 7 |
| `src/lyra/infrastructure/turn_writer/writer.py` | 5 (async, smells, errors, security, types) | 5 |
| `middleware/middleware_stt.py` | 4 (arch, smells, debt, types) | 7 |
| `pipeline/pool_manager.py` | 4 (arch, async, smells, debt) | 4 |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 4 (arch, async, errors, tests) | 6 |
| `outbound/_dispatch.py` | 4 (async, smells, debt, types) | 5 |

---

## Recommended Actions

| Priority | Action | Effort | Impact |
|----------|--------|--------|--------|
| P0 | ~~Convert `AgentRefiner.run_session` to async; offload `input()` and `subprocess.run()` to threads~~ | ~~1–2 days~~ | **FIXED #1431** |
| P1 | Guard `pool.submit()` in `_dispatch_pipeline_result` with try/except to prevent hub-loop crashes | 2–4 hours | **#1448** — Prevents single bad pool from killing the hub |
| P1 | Validate audio-attachment paths before `read_bytes()` / `unlink()` in `llm/attachments.py` | 1 day | **#1447** — Closes path-traversal High |
| P1 | Extract `ResumePublisherPort` and inject into `PipelineContext`; halt new concrete setters on `Hub` until protocols exist | 3–5 days | **#1450** — Closes ADR-048 gap and hub-coupling |
| P1 | ~~Protect bootstrap teardown: wrap each `bus.stop()` / `dispatcher.stop()` / `store.close()` in individual try/except~~ | ~~1 day~~ | **FIXED #1444** |
| P1 | Replace sleep patches in `test_outbound_dispatcher_coverage.py` with an injected `sleep` callable / test clock | 2–3 days | Removes monkey-patching of stdlib |
| P2 | Replace `threading.Lock` with `asyncio.Lock` in `PoolManager` | 4–6 hours | Removes brittle threading primitive in async code |
| P2 | Add `lyra.inbound` to `.importlinter` contracts (layers + independence) | 2–4 hours | Makes inbound boundary enforceable in CI |
| P2 | Backfill bootstrap wiring tests for `agent_store_factory`, `bot_agent_map`, `unified`, `wiring_helpers` | 1–2 weeks | Closes 0–24 % coverage holes |
| P2 | Redact `user_id` / `scope_id` in `AuditConsumer` or move to TRACE-level logger | 4–6 hours | Closes PII exposure finding |

---

## Technical Debt Score

**Score: 59 / 100** — *Fair: notable gaps requiring attention*

| Dimension | Max | Score | Rationale |
|-----------|-----|-------|-----------|
| Architecture conformance | 20 | 11 | ADR-048 stalled (~44 % hub coverage); 5 direct infra imports in `core/hub`; inbound missing from importlinter; outbound→adapters upward violation; packages bidirectional cycle. Stage-axis decomposition is structurally correct but port layer is incomplete. |
| Security posture | 15 | 10 | No critical runtime vulns; 1 High (audio path traversal). Residual risks are config-time (env paths, NATS ACL over-permission #1293) and operational (PII in logs, log injection). GH token dispenser is mature (0600 cache, 15-min TTL, abuse limiter). |
| Code smell density | 15 | 8 | 242 findings; god classes persist (`Hub`, `OutboundRouter`, `PoolManager`, `OutboundEmitter`); 167-line dispatch function; 124-line config setter; significant DRY violations in packages and tests. No 300-line file exemptions violated in `src/lyra/`, but 2 package files exceed cap without exemption. |
| Type safety coverage | 15 | 11 | Strong in core: near 100 % public return types, 2 `# type: ignore` in hub, 0 `cast()` / `assert isinstance()`. Gaps: file-level pyright suppression in bootstrap (`wiring_helpers`), 34 `Any` in `roxabi_nats/_serialize`, duck-typed `object` for TTS/router. |
| Async pattern hygiene | 10 | 5 | 1 Critical blocking bug (`refine` CLI); 8 High (resource leaks, races, blocking I/O in `CliLlmProvider` / `AgentRefiner`). `threading.Lock` used in `PoolManager` inside asyncio context. |
| Error handling discipline | 10 | 6 | 43 % broad catches in hub, but zero bare `except:` or empty `pass`. No `raise ... from e` anywhere; silent data-loss path in `ThreadStore` update; unguarded `pool.submit()` can crash the hub loop. |
| Test quality | 10 | 5 | 88 % core line coverage vs 0–24 % in bootstrap wiring. 48+ `asyncio.sleep` calls create flaky timing. SUT mocking in `test_hub_tts_dispatch.py`. Parametrization nearly absent. |
| Tech debt drain pressure | 5 | 3 | 140 debt markers, but zero TODO/FIXME/HACK/XXX (good hygiene). Stale closed-epic references (#753) and unscheduled Phase 7 cleanup (`_shared_streaming_state` transition) create drift risk. |

---

## Top 10 Quick Wins

| # | Action | Domain | Effort | Impact |
|---|--------|--------|--------|--------|
| 1 | ~~Convert `refine` CLI to async (`asyncio.to_thread` + `create_subprocess_exec`)~~ | Async Patterns | ~~1–2 days~~ | **FIXED #1431** |
| 2 | Guard `pool.submit()` in `Hub.run()` with try/except + log-and-continue | Error Handling | 2–4 hours | **#1448** — Prevents hub consumer loop crash |
| 3 | Sanitize NATS subject segments in `TypingPublisher` (strip `*`, `>`, dots) | Security | 2–4 hours | Closes only Medium injection vector |
| 4 | ~~Wrap each bootstrap `stop()` / `close()` in individual try/except during teardown~~ | Error Handling | ~~4–6 hours~~ | **FIXED #1444** |
| 5 | Replace `threading.Lock` with `asyncio.Lock` in `PoolManager` | Async Patterns | 4–6 hours | Removes brittle anti-pattern |
| 6 | Narrow `hub: object` to `Hub` in `middleware_stt.py` under `TYPE_CHECKING` | Type Safety | 30 min | Deletes partition's only 2 `# type: ignore` |
| 7 | Remove stale `#753 Phase 3` comments from `hub_dispatch.py` + `outbound_router.py` | Tech Debt | 15 min | Stops reader confusion |
| 8 | Extract `_BACKOFF_DELAYS`, `_DEFAULT_STT_TIMEOUT_MS`, `_EVICTION_THROTTLE_DIVISOR` constants | Tech Debt | 30 min | Removes 3 magic literals |
| 9 | Add `lyra.inbound` to `.importlinter` contracts (layers + independence) | Architecture | 2–4 hours | Makes inbound boundary CI-enforceable |
| 10 | Replace SUT mocking in `test_hub_tts_dispatch.py` with a fake `ChannelAdapter` | Test Quality | 2–3 hours | Removes most egregious mock-overuse |
