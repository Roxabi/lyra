# P07 — Infrastructure, Transport, NATS (quality audit 2026-05-26)

> Scope: `src/lyra/infrastructure/**/*.py`, `src/lyra/transport/**/*.py`, `src/lyra/nats/**/*.py`
> Baseline: prior audit 2026-05-18 (`artifacts/analyses/audit-2026-05-18/`)
> Focus: NEW/CHANGED code since 2026-05-18 + gaps the prior audit left unaudited.

---

## Summary

- **P1 (#1278) stage-axis composition is structurally sound** — `lyra.transport` decomposes cleanly into capability files (`_result`, `nats_request_response`, `worker_pool_client`, `turn_publisher`, `typing_publisher`). Domain clients in `lyra.nats` are thin (~60 lines), delegating to `WorkerPoolClient` + codec. Zero circular dependencies (importlinter 8/8 kept).
- **One 3-layer violation regressed** — `LlmClient` (moved to `lyra.llm` post-#1278 T24) reaches through `WorkerPoolClient._transport` for control-plane calls, bypassing CB + routing. This breaks the P1 composition invariant.
- **Turn subsystem (#1331) lacks hexagonal ports** — `TurnStore` and `TurnPublisher` are major new concrete dependencies for core, but no `TurnStoreProtocol` / `TurnPublisherProtocol` exists in `core/ports/`. Core modules import them via `TYPE_CHECKING` or concrete registration.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/llm/llm_client.py` | 156, 186, 209 | **High** | Domain client `LlmClient` calls `self._pool._transport.call()` directly for `reset`, `resume_and_reset`, `switch_cwd`. Bypasses `WorkerPoolClient` circuit breaker, worker routing, and heartbeat validation. | Add a `control_call(subject, payload)` method to `WorkerPoolClient` public API. Remove all `self._pool._transport` accesses from `LlmClient`. |
| `src/lyra/infrastructure/stores/turn_store.py` | 1 (structural) | **Medium** | `TurnStore` is new/refactored (#760/#1331) but has no `TurnStoreProtocol` in `core/stores/`. Core modules (`pool_observer`, `hub`, `cli_pool`) still `TYPE_CHECKING`-import the concrete store. | Create `core/stores/turn_store_protocol.py` covering read-side surface (`get_turns`, `get_last_session`, `get_cli_session`, etc.). Migrate core imports from concrete to Protocol. |
| `src/lyra/core/pool/pool_observer.py` | 52 | **Medium** | `PoolObserver.register_turn_publisher(self, publisher: TurnPublisher)` depends on the concrete `lyra.transport.turn_publisher.TurnPublisher`. Core → transport cross-stage import is not mediated by a port. | Extract `TurnPublisherProtocol` in `core/ports/` with the 5 `publish_*` methods. Implement on `TurnPublisher` (duck-type, no inheritance change needed). |
| `src/lyra/nats/nats_channel_proxy.py` | 160-248 | **Medium** | `send_streaming` hardcodes a keepalive concern (added #687). Keepalive is a cross-cutting capability; should be composable per P2 (#1279) stage-axis. File also exceeds 300 LOC (exempted but not shrinking). | Schedule extraction of `_run_keepalive_loop` into a `KeepaliveCap` reusable capability during P2 outbound stage extraction. |
| `src/lyra/transport/worker_pool_client.py` | 100 | **Low** | `_on_heartbeat` catches broad `except Exception` for JSON decode + worker validation. Adds 1 site to `boundary-broad-catch` slug. | Narrow to `json.JSONDecodeError, UnicodeDecodeError` for decode; wrap `self._registry.record_heartbeat` in its own small try/except. |
| `src/lyra/infrastructure/audit/jetstream_sink.py` | 24 | **Low** | `JetStreamAuditSink` is active and correctly implements `AuditSink` (protocol exists in `core/ports/audit_sink.py`). Provision + emit logic is sound. No runtime leaks. Content was unaudited in prior audit; now verified. | No action required. |

### Notes on prior-audit gaps

| Prior gap | Status | Verdict |
|-----------|--------|---------|
| `infrastructure/audit/jetstream_sink.py` — unaudited | **Audited** | Sound. Implements `AuditSink` protocol. No runtime infra→core leaks. |
| `nats_llm_client.py:416-628` — unaudited | **Deleted** | File removed in #1278 T24/T25/T30. Logic moved to `lyra.llm.llm_client.py` + `lyra.llm.cli_nats_codec.py`. New code audited; `_transport` bypass found (F1). |
| `nats_stt_client.py` + `nats_tts_client.py` `_parse_*_timeout()` — inferred only | **Fixed by deletion** | Thin clients post-P1 no longer contain timeout parsing. `NatsTransport` owns timeout. |
| `packages/roxabi-nats/` + `roxabi-contracts/` hexagonal internal | **Out of P07 scope** | Still not audited; not in `src/lyra/`. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Files in P07 | ~40 `.py` |
| Files changed since 2026-05-18 | ~25 |
| New files added | ~8 (`turn_writer/*`, `typing_*`, `work_scope`, `turn_publisher`, `http_transport`, `agent_store_migrations`, `turn_store_session`) |
| Importlinter contracts | **8 kept, 0 broken** |
| Layer violations (runtime) | **1** (`llm_client` → `_pool._transport`) |
| Stores in `infrastructure/stores/` | 10 |
| Protocols in `core/stores/` | 4 (`agent`, `identity_alias`, `thread`, `pairing`) |
| ADR-048 protocol coverage | **40%** (4/10) |
| Missing protocols (unchanged since prior audit) | 4 (`auth`, `bot_agent_map`, `message_index`, `prefs`) |
| Missing protocols (new/changed code) | 1 (`turn_store`) |
| Files >300 LOC in P07 | 2 (`nats_channel_proxy` 358, `render_event_codec` 388) — both exempted |
| `boundary-broad-catch` in new transport code | 1 (`worker_pool_client._on_heartbeat`) |

---

## Recommendations (prioritized, max 5)

1. **Close the turn-subsystem port gap** — Add `TurnStoreProtocol` + `TurnPublisherProtocol` to `core/ports/` (or `core/stores/`). The turn subsystem (#1331) is the largest new code area and the only ADR-048 gap introduced since the prior audit. Core modules (`PoolObserver`, `LlmClient`, `hub`) depend concretely on `TurnPublisher` and `TurnStore`. Extracting protocols eliminates `TYPE_CHECKING` imports and enables fake injection in tests.

2. **Restore P1 3-layer composition in `LlmClient`** — Remove all `self._pool._transport.call()` accesses (lines 156, 186, 209). Add `WorkerPoolClient.control_call(subject, payload)` so control-plane ops (`reset`, `resume_and_reset`, `switch_cwd`) participate in circuit breaker + heartbeat, consistent with #1278 U2 (composition over inheritance).

3. **Extract `KeepaliveCap` during P2 (#1279)** — The per-stream keepalive loop added to `NatsChannelProxy.send_streaming` (#687) is a cross-cutting capability hardcoded in an outbound adapter proxy. When outbound stage extraction proceeds, factor `_run_keepalive_loop` into a reusable capability applied via composition.

4. **Drain `boundary-broad-catch` in `WorkerPoolClient._on_heartbeat`** — Narrow `except Exception` to specific decode/validation errors. This is a small surgical fix that reduces the `boundary-broad-catch` slug by 1 site, aligning with Epic #1277 success criteria (`boundary-broad-catch` ≤30 post-epic).

5. **Complete ADR-048 for remaining 4 legacy stores** — `auth_store`, `bot_agent_map`, `message_index`, `prefs_store` still lack protocols. This is unchanged since the prior audit but is the last ADR-048 drain work. Schedule after the turn protocols are in place.
