---
title: "Plan: feat(hub) drop turn-writer RO mount — session reads via NATS"
issue: 2309
spec: artifacts/specs/2309-drop-turn-writer-ro-mount-spec.md
complexity: 6/10
tier: F-lite
generated: "2026-08-01T12:00:00Z"
status: approved
---

## Summary

Add core-NATS request/reply query handlers on `factory-turn-writer`, narrow `FACTORY_TURNS` stream subjects to `factory.turns.write`, implement hub `TurnQueryClient` (`TurnStoreProtocol` + `get_resume_count`), stop hub from opening SQLite `turns.db`, then drop the hub RO Volume and ACL-grant the query subjects with `request_reply_flows`.

## Architecture

### Data Flow

Hub drivers/catalog → `TurnQueryClient` → core NATS `factory.turns.get_*` → turn-writer query subscriber → `TurnStore` SQLite. Writes stay JetStream `factory.turns.write` → `TurnWriter` → same SQLite. Stream subjects narrowed so query traffic is not WorkQueue-ingested.

### File × Function Map

| Area | Files |
|------|-------|
| Contracts | `packages/roxabi-contracts/src/roxabi_contracts/turns/subjects.py` (+ tests) |
| Stream | `src/factory/infrastructure/turn_writer/stream_setup.py` |
| Query server | `src/factory/infrastructure/turn_writer/query.py` (new), wire in standalone bootstrap |
| Query client | `src/factory/transport/turn_query_client.py` (new) |
| Hub wire | `bootstrap_stores.py`, `hub_core.py`, `hub_assembly.py`, `hub_clipool_init.py`, `resume_publisher_adapter.py`, `providers.py` |
| Deploy/ACL | `deploy/nats/acl-matrix.json`, regen `auth.conf`, `deploy/quadlet/factory-hub.container`, `deploy/install.sh` |
| Tests | unit tests for query handlers + client; existing turn-writer tests updated |

## Bootstrap Context

No separate analysis (F-lite). Frame + spec approved. Expert review absorbed: stream narrow in S1; `request_reply_flows`; factory-data volume RW overlay invariant.

## Agents

| Agent | Task count | Files |
|-------|-----------|-------|
| backend-dev-A | 4 | contracts subjects, stream_setup, turn_writer/query.py, standalone wire |
| backend-dev-B | 4 | turn_query_client, bootstrap_stores, hub wire, resume adapter |
| devops-A | 3 | acl-matrix + flows, factory-hub.container, install.sh |
| tester-A | 4 | RED/GREEN tests for S1–S2 + smoke notes |
| doc-writer-A | 1 | brief domain/ops note if storage.md still says hub RO |

## Wave Structure

4 waves, max 2 parallel agents. Elapsed ~1–2 days vs ~3 sequential.

| Wave | Trigger | Agents | Tasks |
|------|---------|--------|-------|
| 1 | start | 2 ∥ | backend-dev-A: T1→T2→T3 · tester-A: T4 (RED S1) |
| 2 | Wave 1 done | 2 ∥ | backend-dev-A: T5 (GREEN S1) · backend-dev-B: T6→T7 |
| 3 | Wave 2 done | 2 ∥ | backend-dev-B: T8 · tester-A: T9 (GREEN client) · devops-A: T10→T11 |
| 4 | Wave 3 done | 1–2 | devops-A: T12 · tester-A: T13 smoke checklist · doc-writer-A: T14 |

### Budget — per task

| Task | Items | Class | Est. ops | Split? |
|------|-------|-------|----------|--------|
| T1 contracts subjects | 1 | bounded | 3 | — |
| T2 stream narrow | 1 | bounded | 3 | — |
| T3 query handlers | 1 | judgmental | 6 | — |
| T4 RED S1 tests | 1 | judgmental | 5 | — |
| T5 wire query on turn-writer | 1 | bounded | 3 | — |
| T6 TurnQueryClient | 1 | judgmental | 6 | — |
| T7 hub bootstrap no SQLite | 1 | judgmental | 6 | — |
| T8 adapter + assembly rewire | 1 | bounded | 4 | — |
| T9 GREEN client tests | 1 | judgmental | 5 | — |
| T10 ACL matrix + flows | 1 | judgmental | 5 | — |
| T11 drop hub RO volume | 1 | bounded | 3 | — |
| T12 install.sh + regen auth | 1 | bounded | 3 | — |
| T13 smoke / verify AC | 1 | exploratory | 8 | — |
| T14 docs note | 1 | trivial | 2 | — |

**Total estimated ops: ~62**

### Budget — per agent instance

| Instance | Tasks | Σ ops | Subjects | Split? |
|----------|-------|-------|----------|--------|
| backend-dev-A | T1–T3, T5 | 15 | contracts, stream, query | — |
| backend-dev-B | T6–T8 | 16 | client, bootstrap, wire | — |
| devops-A | T10–T12 | 11 | acl, deploy | — |
| tester-A | T4, T9, T13 | 18 | tests | — |
| doc-writer-A | T14 | 2 | docs | — |

## Consistency Report

- Criteria covered: 9/9 (stream, bootstrap, volume, ACL, fixture resume, catalog, unit tests, smoke)
- Uncovered criteria: none
- Tasks without spec backing: none
- Gold plating exemptions: contracts package edit is required by subject_literals policy (not gold plate)

## Micro-Tasks

### Slice V1: Query surface + stream narrow (S1)

#### Task 1: Add turn query subject constants [P] → backend-dev-A
- **File:** `packages/roxabi-contracts/src/roxabi_contracts/turns/subjects.py`
- **Snippet:** `get_cli_session: Literal["factory.turns.get_cli_session"] = "factory.turns.get_cli_session"` (+ siblings per spec table)
- **Verify:** `uv run pytest packages/roxabi-contracts/tests/test_turn_subjects.py -q`
- **Expected:** pass
- **Time:** 5 min · **Difficulty:** 1
- **Traces:** SC stream/ACL, N1 · **Phase:** GREEN · **Subject:** contracts · **Instance:** backend-dev-A

#### Task 2: Narrow FACTORY_TURNS stream subjects → backend-dev-A
- **File:** `src/factory/infrastructure/turn_writer/stream_setup.py`
- **Snippet:** `subjects=["factory.turns.write"]` in `_stream_config`; update module docstring
- **Verify:** `rg 'subjects=\["factory.turns' src/factory/infrastructure/turn_writer/stream_setup.py`
- **Expected:** only write subject
- **Time:** 5 min · **Difficulty:** 2
- **Traces:** SC stream · **Phase:** GREEN · **Subject:** stream · **Instance:** backend-dev-A

#### Task 3: Implement turn-writer query handlers → backend-dev-A
- **File:** `src/factory/infrastructure/turn_writer/query.py` (new)
- **Snippet:** `class TurnQueryServer: async def start(self, nc): ... subscribe each SUBJECTS.get_* ; reply JSON mirroring TurnStore rows`
- **Verify:** import + unit test later; `python -c "from factory.infrastructure.turn_writer.query import TurnQueryServer"`
- **Expected:** import ok
- **Time:** 25 min · **Difficulty:** 4
- **Traces:** S1, N1, U1–U4 · **Phase:** GREEN · **Subject:** query · **Instance:** backend-dev-A

#### Task 4: RED tests for query handlers [P] → tester-A
- **File:** `tests/.../test_turn_query_server.py` (new; match existing turn_writer test dir)
- **Snippet:** happy `get_cli_session` + missing session null + invalid JSON error envelope
- **Verify:** `uv run pytest …/test_turn_query_server.py -q` (expect fail until T3 complete if RED-first; else pass after T3)
- **Expected:** covers happy/missing
- **Time:** 20 min · **Difficulty:** 3
- **Traces:** SC unit tests · **Phase:** RED · **Subject:** tests · **Instance:** tester-A

#### Task 5: Wire TurnQueryServer into turn-writer standalone → backend-dev-A
- **File:** `src/factory/bootstrap/standalone/worker_standalone.py` (or turn-writer bootstrap path)
- **Snippet:** after TurnStore connect + before/alongside TurnWriter.start, start query server on core `nc`
- **Verify:** `rg TurnQueryServer src/factory/bootstrap`
- **Expected:** wired
- **Time:** 15 min · **Difficulty:** 3
- **Traces:** S1 · **Phase:** GREEN · **Subject:** query · **Instance:** backend-dev-A

#### RED-GATE V1 → tester-A
- **Verify:** T4 tests pass against T3+T5
- **Phase:** RED-GATE

### Slice V2: TurnQueryClient + hub wire (S2)

#### Task 6: Implement TurnQueryClient → backend-dev-B
- **File:** `src/factory/transport/turn_query_client.py` (new)
- **Snippet:** `class TurnQueryClient: def __init__(self, nc, timeout=3.0): ... async def get_cli_session(...): nc.request; fail-soft`
- **Verify:** unit tests T9
- **Expected:** implements TurnStoreProtocol + get_resume_count
- **Time:** 25 min · **Difficulty:** 4
- **Traces:** S2, U1 · **Phase:** GREEN · **Subject:** client · **Instance:** backend-dev-B

#### Task 7: Hub open_stores without SQLite TurnStore → backend-dev-B
- **File:** `src/factory/bootstrap/bootstrap_stores.py` (+ callers of StoreBundle.turn)
- **Snippet:** `turn: TurnStoreProtocol` optional/None; hub path supplies TurnQueryClient later — **or** inject client factory; never call `factory_turns_db_path()` on hub
- **Verify:** `rg factory_turns_db_path src/factory/bootstrap` shows only turn-writer standalone (not hub open_stores)
- **Expected:** hub open_stores no turns.db
- **Time:** 20 min · **Difficulty:** 4
- **Traces:** SC bootstrap · **Phase:** GREEN · **Subject:** bootstrap · **Instance:** backend-dev-B

#### Task 8: Rewire hub_core / assembly / adapter → backend-dev-B
- **Files:** `hub_core.py`, `hub_assembly.py`, `hub_clipool_init.py`, `resume_publisher_adapter.py`, `providers.py`
- **Snippet:** `TurnPublisherAdapter(publisher, query_client)`; `set_turn_store(query_client)` on drivers; unified mode shares in-proc TurnStore if single process
- **Verify:** `rg set_turn_store src/factory/bootstrap`
- **Expected:** no stores.turn SQLite on hub path
- **Time:** 20 min · **Difficulty:** 4
- **Traces:** S2, U1–U4 · **Phase:** GREEN · **Subject:** wire · **Instance:** backend-dev-B

#### Task 9: GREEN client tests → tester-A
- **File:** `tests/.../test_turn_query_client.py`
- **Snippet:** mock nc.request → ok / TimeoutError / NoRespondersError → null/empty/0
- **Verify:** `uv run pytest …/test_turn_query_client.py -q`
- **Expected:** pass
- **Time:** 15 min · **Difficulty:** 3
- **Traces:** SC unit tests · **Phase:** GREEN · **Subject:** tests · **Instance:** tester-A

#### RED-GATE V2 → tester-A
- **Verify:** T9 + bootstrap invariant tests green
- **Phase:** RED-GATE

### Slice V3: ACL + deploy (S3)

#### Task 10: ACL matrix + request_reply_flows → devops-A
- **File:** `deploy/nats/acl-matrix.json`
- **Snippet:** hub publish list += all `factory.turns.get_*` / list_* subjects; `request_reply_flows` += hub→turn-writer per subject (or grouped if matrix allows wildcards)
- **Verify:** project ACL regen + check scripts (stack.yml gates)
- **Expected:** drift-clean
- **Time:** 20 min · **Difficulty:** 3
- **Traces:** SC ACL · **Phase:** GREEN · **Subject:** acl · **Instance:** devops-A

#### Task 11: Drop hub RO Volume → devops-A
- **File:** `deploy/quadlet/factory-hub.container`
- **Snippet:** remove Volume line for `turn-writer/:ro` and related comments that claim hub is RO reader of turns.db
- **Verify:** `rg turn-writer deploy/quadlet/factory-hub.container` → no Volume
- **Expected:** no match for Volume bind
- **Time:** 5 min · **Difficulty:** 1
- **Traces:** SC volume · **Phase:** GREEN · **Subject:** deploy · **Instance:** devops-A

#### Task 12: install.sh comment + auth.conf regen → devops-A
- **Files:** `deploy/install.sh`, `deploy/nats/auth.conf` (generated)
- **Snippet:** pre-create turns.db still for turn-writer; drop “hub bind-mounts it” wording; regen auth from matrix
- **Verify:** `rg 'hub bind|both factory-turn-writer and factory-hub' deploy/install.sh` empty; auth drift check
- **Expected:** clean
- **Time:** 10 min · **Difficulty:** 2
- **Traces:** SC ACL/deploy · **Phase:** GREEN · **Subject:** deploy · **Instance:** devops-A

### Slice V4: Smoke + docs (S4)

#### Task 13: Smoke / AC verification checklist → tester-A
- **File:** optional `artifacts/evidence/2309-smoke.md` or PR body checklist
- **Snippet:** fixture session → get_cli_session match; cold hub restart resume; FD check; dashboard non-empty
- **Verify:** manual / staging
- **Expected:** all SC boxes checkable
- **Time:** 30 min · **Difficulty:** 3
- **Traces:** SC smoke · **Phase:** GREEN · **Subject:** tests · **Instance:** tester-A

#### Task 14: Docs note (storage / CURRENT) → doc-writer-A
- **File:** `docs/architecture/storage.md` or CURRENT pointer if still says hub RO turns.db
- **Snippet:** hub is bus client for turns reads; sole SQLite owner turn-writer
- **Verify:** `rg 'turns.db.*ro|RO mount' docs/architecture -i` no stale hub-RO claim
- **Expected:** updated or N/A
- **Time:** 10 min · **Difficulty:** 1
- **Traces:** ops note · **Phase:** GREEN · **Subject:** docs · **Instance:** doc-writer-A

## Task Seeding Blueprint

### Wave 1 — no deps, 2 agents ∥

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T1 | backend-dev-A | — | contracts |
| T2 | backend-dev-A | T1 | stream |
| T3 | backend-dev-A | T1 | query |
| T4 | tester-A | — | tests |

### Wave 2 — after Wave 1

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T5 | backend-dev-A | T3 | query |
| T6 | backend-dev-B | T1 | client |
| T7 | backend-dev-B | T6 | bootstrap |

### Wave 3 — after Wave 2

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T8 | backend-dev-B | T7 | wire |
| T9 | tester-A | T6 | tests |
| T10 | devops-A | T5,T8 | acl |
| T11 | devops-A | T8 | deploy |

### Wave 4 — after Wave 3

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T12 | devops-A | T10,T11 | deploy |
| T13 | tester-A | T9,T12 | tests |
| T14 | doc-writer-A | T11 | docs |

## Task IDs

<!-- Seeded in session by /plan; /implement re-attaches via this list. -->
- T1: plan-2309-t1 — contracts
- T2: plan-2309-t2 — stream
- T3: plan-2309-t3 — query
- T4: plan-2309-t4 — tests
- T5: plan-2309-t5 — query
- T6: plan-2309-t6 — client
- T7: plan-2309-t7 — bootstrap
- T8: plan-2309-t8 — wire
- T9: plan-2309-t9 — tests
- T10: plan-2309-t10 — acl
- T11: plan-2309-t11 — deploy
- T12: plan-2309-t12 — deploy
- T13: plan-2309-t13 — tests
- T14: plan-2309-t14 — docs

## Ref patterns

- `src/factory/transport/nats_request_response.py` — request/reply Result + sanitize
- `src/factory/infrastructure/turn_writer/writer.py` + `stream_setup.py` — turn-writer lifecycle
- `src/factory/transport/turn_publisher.py` — hub write client shape
- `deploy/nats/acl-matrix.json` `request_reply_flows` — hub→clipool / hub→voice patterns
- `packages/roxabi-contracts/src/roxabi_contracts/turns/subjects.py` — existing turn_write
