---
title: ADR-055 Code Review — Deferred Follow-ups
source_commit: dfa221b
source_review_date: 2026-04-24
reviewers: [architect, devops, product-lead]
adr: docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx
status: open
---

# ADR-055 — Deferred Follow-ups

14 non-blocking findings from the multi-agent code review of ADR-055. The 7 blockers were resolved in commit `765f4dc`; this file tracks what was intentionally deferred.

## Warnings (10)

### W1 — voiceCLI runtime root unconfirmed
**Severity:** issue | **Confidence:** 82% | **Reviewer:** product-lead
**Location:** ADR-055 D4 / follow-up 1

ADR assumes voiceCLI's runtime root is `~/.voicecli/`, but `PROD-MIGRATION-STRATEGY.md §1c` lists the current state dir as `~/.local/state/voicecli/`. If voiceCLI's code reads nkeys from `~/.local/state/voicecli/nkeys/`, the D4 migration path (`mv ... ~/.voicecli/nkeys/`) will silently break NATS auth at Phase 2 start.

**Action:** verify voiceCLI's actual runtime root from its codebase before Phase 2 kickoff. Either reconcile to `~/.voicecli/` (preferred — matches `~/.lyra/` precedent) or update D4 to match current reality.

### W2 — Phase 4 auth.conf merge procedure hand-waved
**Severity:** issue | **Confidence:** 85% | **Reviewer:** devops
**Location:** ADR-055 D2 Phase 4 consolidation path

Merged auth.conf assembly is stated but not prescribed. Missing: explicit `nats-server --config-check` dry-run step, TLS cert reuse-vs-regen decision (reuse `/etc/nats/certs/` via volume mount?), parallel-run validation window before retiring host NATS.

**Action:** expand Phase 4 consolidation steps in `PROD-MIGRATION-STRATEGY.md` with: (a) config-check dry-run, (b) TLS cert handling, (c) 5-min smoke window with `nats-cli` auth tests before retiring per-project containers.

### W3 — D3 network migration downtime unacknowledged
**Severity:** issue | **Confidence:** 90% | **Reviewer:** devops + product-lead
**Location:** ADR-055 D3 migration window

Moving containers from `<project>.network` to `roxabi.network` at Phase 4 requires `podman rm` + recreate — Quadlet does not hot-swap `Network=`. Per-service outage ~10s; end-user bot downtime not scheduled.

**Action:** add "Network migration requires container recreation" note to D3. Document per-service outage window. Consider blue/green (second container on `roxabi.network` alongside old) if any service cannot tolerate 10s.

### W4 — D7 "Linux kernel hosts drivers" lacks governance substance
**Severity:** issue | **Confidence:** 78% | **Reviewer:** architect
**Location:** ADR-055 D7 rationale

The kernel analogy justifies origin-based hosting but skips the review process that makes the analogy work. Missing: who approves changes to `lyra/deploy/quadlet/nats.container` or `scripts/deploy-lib.sh` when those changes affect downstream projects.

**Action:** add a governance paragraph to D7 — changes to tagged shared-infra files require changelog notice to downstream project maintainers before merge. Consider `CODEOWNERS`-style note in CLAUDE.md naming the shared-infra surface (`lyra/deploy/quadlet/nats.container`, `lyra/deploy/quadlet/roxabi.network`, `lyra/scripts/deploy-lib.sh`).

### W5 — `roxabi.network` naming rationale thin
**Severity:** issue | **Confidence:** 80% | **Reviewer:** architect
**Location:** ADR-055 D3

D1 bans `roxabi-` prefix for container images (per-project artifacts). D3 keeps `roxabi.network` (multi-consumer infra). The principle is sound but the ADR doesn't articulate why — inviting the exact question it should preempt.

**Action:** add one sentence to D3 rationale: "`roxabi.network` is correctly named with the shared namespace because it is a genuine multi-consumer infra resource (shared at Phase 4), unlike container images which are per-project artifacts."

### W6 — `voicecli-nats.container` template stub missing
**Severity:** issue | **Confidence:** 80% | **Reviewer:** devops
**Location:** ADR-055 D2

An operator reading ADR-055 alone cannot write `voicecli-nats.container`. Missing: Podman secret naming convention confirmed, JetStream flag (`-js`) decision, TLS config for localhost-only traffic, port mapping template.

**Action:** add a minimal `voicecli-nats.container` stub to the ADR (or cross-reference where it will land in voiceCLI's repo). Confirm `<project>-nats-<identity>` secret naming and whether JetStream is required per-project or only for the Phase 4 shared NATS.

### W7 — Lyra retirement path unaddressed
**Severity:** issue | **Confidence:** 75% | **Reviewer:** product-lead
**Location:** ADR-055 Consequences

D7 commits every downstream project to deploy-time dependency on `~/projects/lyra/`. If Lyra is ever deprecated, renamed, or absorbed, every consumer breaks. Linux kernel analogy holds only because kernel is not going away.

**Action:** add "Lyra retirement path" note in Consequences: extraction trigger (e.g., "if Lyra's scope narrows to AI-agent-only, promote `deploy/quadlet/` + `scripts/deploy-lib.sh` to a new `roxabi-infra` repo as a one-time migration"). Name the trigger condition; don't require active monitoring.

### W8 — Follow-up 4/2 sequencing circular
**Severity:** issue | **Confidence:** 78% | **Reviewer:** devops
**Location:** ADR-055 follow-ups 2 + 4

Follow-up 4 (extract `deploy-lib.sh`) says "update Lyra's deploy script to source it as proof-of-concept." But validation requires voiceCLI (follow-up 2) to be deployed. Either sequence is circular as-written.

**Action:** explicit ordering — (4) extract lib + update Lyra's deploy + validate Lyra still deploys cleanly on M₂ → (2) write voiceCLI units using the library. Mark follow-up 4 as prerequisite to follow-up 2 when issues are filed.

### W9 — Phase 4 end-user bot downtime not acknowledged
**Severity:** issue | **Confidence:** 80% | **Reviewer:** product-lead
**Location:** ADR-055 Consequences

Phase 4 container restarts cause ~5 min Telegram/Discord bot outage. Not mentioned in Consequences; no low-traffic window scheduled.

**Action:** add "User impact" line to Consequences: "Phase 4 container restarts cause a brief bot outage (~5 min per project). Schedule during low-traffic window." Per-project runbooks own the actual window selection.

### W10 — Follow-ups 7+8 are audits, not actionable issues
**Severity:** suggestion (blocking for Phase 3 planning) | **Confidence:** 85% | **Reviewer:** product-lead
**Location:** ADR-055 follow-ups 7 + 8

Follow-up 7 ("Confirm imageCLI/llmCLI NATS usage") and follow-up 8 ("Confirm roxabi-vault daemon status") have undefined acceptance criteria. Phase 4 auth.conf scope depends on their outcome.

**Action:** file real issues with explicit acceptance criteria (e.g., "grep imageCLI repo for `nats.` imports; if present, document which subjects"). Mark as Phase 3 prerequisites.

## Nitpicks (4)

### N1 — Port registry SSoT doesn't exist yet
**Severity:** nitpick | **Confidence:** 92% | **Reviewer:** product-lead

Open Question 3 (was 4 before B7 fix) points to `PROD-MIGRATION-STRATEGY.md §1c` as port registry SSoT, but §1c contains the supervisord audit table, not a port table. Next project will pick a port ad hoc.

**Action:** add a "Port assignments" subsection to `PROD-MIGRATION-STRATEGY.md §1c` seeded with 4222 (host), 4223 (Lyra), 4224 (voiceCLI) + instructions for claiming next port.

### N2 — D1 single-image vs multi-image inconsistency
**Severity:** nitpick | **Confidence:** 75% | **Reviewer:** devops

Migration doc §2b shows `localhost/voicecli:latest` (single image); ADR-055 D5 example uses `localhost/voicecli-tts:latest` + `localhost/voicecli-stt:latest` (multi-image) but `IMAGE="localhost/voicecli:latest"` in the sourced variables. Unclear whether convention is one-image-multiple-containers or image-per-service.

**Action:** clarify in D1: one image per project with different `Exec=` per container OR separate images per service. Probable answer: one image, multiple containers (matches Lyra's single `localhost/lyra:latest` with `lyra-hub`, `lyra-telegram`, `lyra-discord`).

### N3 — Alt-A "half-day" estimate not derived
**Severity:** thought | **Confidence:** 65% | **Reviewer:** architect

ADR-055 Alt-A rejection asserts Phase 4 migration is "half-day of work"; `PROD-MIGRATION-STRATEGY.md §8` says "1–2 days." Minor inconsistency.

**Action:** align. Likely: "half-day for mechanical migration steps + remainder for stabilization window" footnote.

### N4 — Deferred tier (forge/intel/idna/live/vault) not advanced
**Severity:** thought | **Confidence:** 72% | **Reviewer:** product-lead

ADR-055 slots deferred tier into D3's network topology but doesn't advance their Quadlet readiness. Phase 5 (supervisord retirement) remains as blocked as before.

**Action:** either (a) accept deferral explicitly in the relationship table, or (b) add a "Phase 2.5" sprint to `PROD-MIGRATION-STRATEGY.md` that produces Dockerfile + Quadlet unit estimates per deferred-tier project.

## Execution Order (Recommended)

When picking these up, rough dependency order:

1. **W1** — verify voiceCLI runtime root (blocks Phase 2 kickoff)
2. **N1** — create port registry table (needed before voiceCLI claims 4224)
3. **W10** — resolve follow-ups 7+8 (audit imageCLI/llmCLI NATS + vault daemon status; unblocks Phase 3 planning)
4. **W6** — `voicecli-nats.container` stub (unblocks Phase 2 implementation)
5. **W8** — lock follow-up 4/2 ordering
6. **W2, W3, W9** — Phase 4 rigor (auth.conf merge, network recreate, bot downtime)
7. **W4, W5, W7, N2, N3, N4** — ADR polish; can batch

## Linked Commits

- `765f4dc` — ADR-055 initial + review fix (blockers B1–B7 resolved)
- `dfa221b` — ADR-055 status flipped to Accepted
