---
title: "feat(hub): drop turn-writer RO mount — session reads via NATS"
issue: 2309
status: approved
tier: F-lite
date: 2026-08-01
---

## Problem

After #2308 / ADR-075, hub **writes** to turns (`set_cli_session`, log_turn, …) already go over NATS `factory.turns.write` → `factory-turn-writer` (sole SQLite owner). **Reads** still go the other way: hub opens `TurnStore` against a **RO** bind of `~/.roxabi/factory/turn-writer/` (including `turns.db`) so Claude/Omp resume can call `get_cli_session`.

That RO mount is still shared filesystem for cross-process data. It conflicts with the ecosystem-plane direction (ADR-068): one owner process, everyone else is a bus client; no shared data via mounts. It also leaves a deploy coupling (hub unit must know turn-writer data layout) and a residual failure mode (RO mount wrong → resume broken even when the writer is healthy).

**Why now:** write path is fixed; the remaining shared-FS read path is the last hub touch of `turns.db`. Closing it completes the sole-writer story for turns.

## Who

- **Primary:** factory-hub process (and Claude/Omp drivers using `get_cli_session` for `queue_resume`) — must resume CLI sessions after cold restart without local SQLite.
- **Secondary:** M₁ operator / deploy (`factory-hub.container` volumes, ACL matrix) — one fewer RO volume; turn-writer remains sole FD owner of `turns.db`.

## Constraints

- **Sole writer remains turn-writer** — hub must not open `factory_turns_db_path()` for turns after this change (reads or writes).
- **Reuse #2308 / ADR-075 patterns** — JetStream write path stays; this adds request/reply **read** RPC, not a second write channel.
- **Minimal RPC surface for this issue:** `factory.turns.get_cli_session` (request/reply) — `{session_id}` → `{cli_session_id|null}`. Optional later: `get_resume_count`, list turns.
- **Identity / ACL:** hub publishes requests (identity hub / `_inbox.hub.`); turn-writer subscribes + `allow_responses` for replies. ACL matrix + `auth.conf` must stay drift-clean (matrix-driven gen).
- **Deploy gate:** remove `turn-writer/:ro` Volume from `factory-hub.container`; volumes_table / install pre-create notes updated only as needed for hub no longer binding that path.
- **Bootstrap:** hub composition must not construct a SQLite `TurnStore` for turns reads; wire a NATS query client instead (drivers already depend on a narrow `get_cli_session` protocol).
- **Axis:** stage/pipeline stays clean — thin transport client + turn-writer handler; no per-adapter duplication of session lookup.

## Out of Scope

- Expanding turn query API beyond what resume needs now (`get_resume_count` / list / analytics) unless a compile-time hub call still requires SQLite.
- Changing write path / `TurnPublisher` / JetStream consumer model (owned by #2308 / ADR-075).
- Moving `turns.db` off SQLite or changing turn-writer ownership.
- Adapter-local session stores (Telegram/Discord/web) unrelated to CLI resume IDs.
- Full multi-region turns replication.

## Premise Validity

**Success in 6 months:** hub unit has no `turn-writer` Volume; hub process open FDs never include `turns.db`; Claude/Omp `queue_resume` works after cold restart; ACL matrix + auth.conf drift-clean; smoke web chat Lyra + resume path green.

**Failure in 6 months (falsifiable):** within **1 release after merge**, either (a) `factory-hub` still ships a `turn-writer/:ro` Volume or open FDs show `turns.db`, or (b) `queue_resume` fails after cold restart while write path via TurnPublisher still works — i.e. reads regressed without a shared mount fallback.

**Simplest alternative:** keep the RO mount for reads (status quo post-#2308); only writes use the bus.
**Why not simplest:** shared FS still couples hub deploy topology to turn-writer data dir and violates “bus is the contract” for cross-process access; RO mount remains a second, silent channel that ops must keep correct.

## Complexity

**Tier: F-lite** — issue label `size:F-lite`; clear scope (one RPC + hub client swap + ACL + drop volume); single domain (turns ownership / hub↔turn-writer); reuses established NATS identity and TurnPublisher patterns from #2308.

Signals observed: `size:F-lite`; design enumerated in issue body; no multi-domain unknowns if RPC stays at `get_cli_session` (+ any residual hub SQLite read that still forces the mount).
