# Quality Audit Summary — Lyra (staging)

## Executive Summary

- Baseline: 2026-04-22 debt score 72/100
- Files audited: 3,291
- Issues found: 1,258
- P0 (Critical): 2
- P1 (High): 172
- P2 (Medium): 664
- P3 (Low): 427
- Axial drift: 11 contracts kept, 0 broken
- Cocoindex cross-domain: confirmed

## Domain Breakdown

| Domain | Files | P0 | P1 | P2 | P3 |
|--------|-------|----|----|----|----|
| architecture | 368 | 0 | 13 | 53 | 48 |
| async-patterns | 356 | 0 | 3 | 20 | 32 |
| code-smells | 694 | 1 | 55 | 154 | 82 |
| error-handling | 372 | 1 | 7 | 48 | 31 |
| security | 380 | 0 | 0 | 8 | 18 |
| tech-debt | 377 | 0 | 43 | 177 | 114 |
| test-quality | 366 | 0 | 27 | 62 | 24 |
| type-safety | 378 | 0 | 24 | 142 | 78 |

## Top 10 Critical Issues

| rank | severity | file | line | description |
|------|----------|------|------|-------------|
| 1 | P0 | `src/lyra/core/pool/pool_processor_exec.py` | 106 | `process_one` is 165 lines (`noqa: C901, PLR0915`); combines streaming vs non-streaming bifurcation, processor pre/post hooks, session ID updates, error handling, and turn logging in one method |
| 2 | P0 | `src/lyra/infrastructure/stores/turn_store.py` | 214 | `_log_turn` catches `Exception`, logs with `log.exception`, and returns without re-raising. Caller (`TurnWriter._handle_log_turn`) proceeds to `ack()` the JetStream message, so a failed SQLite write permanently loses the conversation turn |
| 3 | P1 | `src/lyra/core/ports/llm.py` | 13 | Runtime import `from lyra.core.agent.agent_config import ModelConfig` violates driven-port purity rule (ports must be self-contained; no cross-core imports) |
| 4 | P1 | `src/lyra/core/ports/llm.py` | 14 | Runtime import `from lyra.core.messaging.events import LlmEvent` violates driven-port purity rule (ports must be self-contained; no cross-core imports) |
| 5 | P1 | `src/lyra/core/agent/agent_refiner.py` | 167 | `AgentRefiner.__init__` parameter `store` is typed with `AgentStore` (concrete infrastructure class) instead of `AgentSeederTarget` protocol. Couples agent domain to `lyra.infrastructure` |
| 6 | P1 | `src/lyra/core/auth/authenticator.py` | 19 | `AuthStore` imported from `lyra.infrastructure.stores.auth_store` under `TYPE_CHECKING`; no `AuthStoreProtocol` exists in `core/stores/` to satisfy ADR-048/059 dependency inversion |
| 7 | P1 | `src/lyra/core/auth/authenticator.py` | 20 | `IdentityAliasStore` imported from `lyra.infrastructure.stores.identity_alias_store` under `TYPE_CHECKING`; `IdentityAliasStoreProtocol` exists but concrete class is used instead |
| 8 | P1 | `src/lyra/bootstrap/factory/agent_factory.py` | 180 | `SessionTools` built directly with `WebIntelScraper()` and `VaultCli()` — business logic in bootstrap layer, violating "orchestration only" invariant |
| 9 | P1 | `src/lyra/inbound/dispatcher.py` | 8 | Runtime import from forbidden `adapters` layer into `inbound` stage axis. `.importlinter` ignores it as `DEBT:inbound-adapters-transition`, but it remains active code |
| 10 | P1 | `src/lyra/infrastructure/stores/turn_store_session.py` | 79 | `_set_cli_session` catches `Exception`, logs, and returns without re-raising. `TurnWriter` acks the message but the CLI session ID is never persisted, breaking `--resume` after restart |

## Cross-Domain Validations

- **Axial drift**: All 11 import-linter contracts are kept (0 broken). Key invariants verified: transport streaming core llm/nats infrastructure adapters bootstrap layer ordering; core/stores protocols do not import SQLite drivers; commands do not import infrastructure directly; agents do not import composition root; shared floating modules remain peer-isolated; inbound stages do not import adapters (stage-axis invariant, ADR-073/#1287); production code does not import `tests.fakes`.
- **Cocoindex**: Cross-domain semantic consistency confirmed across architecture ADRs, security specs, and deployment docs. Bot credential delivery (ADR-074/#1057), GitHub app identity (#1078), and Podman secret plane patterns are aligned between docs, specs, and code.

## Recommendations

1. **P0 — Refactor `process_one`** (effort: high): Split into `_process_streaming` and `_process_non_streaming` helpers, extract session-ID management, error handling, and turn logging into discrete sub-methods.
2. **P0 — Harden `_log_turn` error contract** (effort: medium): Re-raise after logging, or return a failure signal that prevents `ack()` in `TurnWriter`. Add a DLQ/retention path for un-persisted turns.
3. **P1 — Co-locate LLM port value objects** (effort: medium): Move `ModelConfig` and `LlmEvent` (or minimal stubs) into `core/ports/llm_types.py` so the driven port is self-contained.
4. **P1 — Introduce `AuthStoreProtocol` and `IdentityAliasStoreProtocol`** (effort: low): Swap concrete imports for protocol references in `authenticator.py` to satisfy ADR-048/059.
5. **P1 — Extract `SessionTools` factory** (effort: medium): Move `WebIntelScraper` and `VaultCli` construction out of bootstrap into a dedicated `services` or `integrations` layer; bootstrap should only inject pre-built instances.
6. **P1 — Resolve inbound→adapters layer violation** (effort: medium): Move `PushGuardDeps` and `push_to_hub_guarded` into `core` or `inbound` so `inbound` does not import `adapters`.
7. **P1 — Harden turn-store session methods** (effort: medium): Make `_set_cli_session` and `_end_session` propagate exceptions or return explicit `Result` so `TurnWriter` can decide whether to `ack()` or `nack()`.
8. **P2 — Address type-safety backlog** (effort: medium): 142 P2 type-safety issues (primarily `Any` annotations, missing `TYPE_CHECKING` guards, and private-module imports) should be batch-fixed in a dedicated cleanup sprint.
9. **P2 — Reduce bootstrap function sizes** (effort: medium): 8 bootstrap functions exceed 80 lines; extract platform-pair DRY violations (Telegram/Discord wiring) into shared helpers.
10. **P3 — Test suite hygiene** (effort: low): 24 P3 test-quality issues (flaky `sleep()` waits, missing assertions, duplicated helpers) can be incrementally fixed during normal test maintenance.

## Next Steps

1. Create `/dev` worktree for P0 remediation (refactor `process_one` + `_log_turn` error contract).
2. Queue P1 architecture fixes (LLM port purity, auth protocols, bootstrap layer boundaries) as a follow-up `refactor` PR.
3. Schedule a weekly 30-min "P2/P3 type-safety & test hygiene" batch-cleanup session.
4. Update `docs/ARCHITECTURE.md` with any new invariants discovered during the audit.
