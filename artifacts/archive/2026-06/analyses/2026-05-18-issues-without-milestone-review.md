---
title: Review — Open issues without milestone
date: 2026-05-18
type: analysis
status: review
---

# Review — Open issues sans milestone (48 issues)

## Verdict global

| Status | Count | % | Action |
|--------|-------|---|--------|
| **KEEP** | 35 | 73% | Issue valide, premise tient, planifier |
| **ADJUST** | 7 | 15% | Body/AC à mettre à jour (file moved, sibling closed, scope partial) |
| **DONE** | 3 | 6% | Close immédiat — travail déjà landé |
| **STALE** | 3 | 6% | Close as not planned ou park backlog |
| **UNCLEAR** | 0 | 0% | — |
| **Total** | 48 | 100% | |

## Insights transverses

| # | Insight | Impact |
|---|---------|--------|
| 1 | Production bug live: `[scraping unavailable]` (#1052) bloqué 4 niveaux dans epic #1044 | Priorité absolue — affecte les users |
| 2 | Parent #1102 fermé 2026-05-15 → 3 follow-ups (#1133/#1134/#1135) sont DONE | Triple close immédiat |
| 3 | #1055 fermé (PRs #1062 + #1073) → #1058 + #1059 désormais débloqués (NATS KV chain) | Slot scheduling immédiat |
| 4 | #1151 fermé aujourd'hui (PR #1223) → epic #1158 reste à 2 sous-tâches | Re-check à la fermeture de #1149/#1150 |
| 5 | Bodies stale après renames/PRs : #1048 (`NatsLlmDriver`→`NatsLlmClient`), #1059 (block lifted), #1204 (#1142 closed) | Sed-pass 7 issues |
| 6 | #1198 titre dit "3 follow-ups" mais body en liste 5 | Retitle |
| 7 | #1194 décision A/B/C en attente humaine — 1 phrase débloque tout | Decision Protocol owner |
| 8 | #1079 (TurnStoreProtocol) confirmé KEEP : 6 `ignore_imports` debt annotations dans `.importlinter` | Pas pre-emptable, refactor nécessaire |

---

## Groupé par Epic Parent

### Epic #1044 — lyra-code-worker fleet [P1-high · 11 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1044 | **EPIC** — workers fleet | KEEP | Garder, scheduler #1203 first |
| #1203 | JetStream JOBS stream + DLQ | KEEP | Critical path — schedule first |
| #1046 | lyra-code-worker Quadlet scaffold | KEEP | Depends on #1203 |
| #1047 | lyra worker bootstrap + JobHandler | KEEP | Depends on #1046 |
| #1048 | NATS-LLM proxy worker→clipool | **ADJUST** | Body: `NatsLlmDriver`→`NatsLlmClient` (deleted PR #1207 / commit `0338b4a6`) |
| #1050 | migrate web-intel.scrape | KEEP | Depends on #1047 |
| #1051 | migrate vault.add | KEEP | Depends on #1047 |
| #1052 | vault.add_from_url composite ⚠️ | KEEP | **Fix prod bug `[scraping unavailable]`** |
| #1053 | remove VaultAddProcessor | KEEP | Depends on #1052 |
| #1054 | docs(architecture) worker fleet | KEEP | Schedule co/post-impl |
| #1204 | ACL llm-worker inbox audit | **ADJUST** | Body: #1142 closed (commit `f7f4e21c`); blocked on #1046 landing |

**Chain critique** : #1203 → #1046 → #1047 → (#1048, #1050, #1051) → #1052 → #1053 → #1054.
**Milestone candidate** : "Worker fleet v1" — 11 issues, scope cohérent.

---

### Epic #1061 — BlobStore [P2 · graph:defer · 7 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1061 | **EPIC** — BlobStore | **ADJUST** | Body strategy: NATS Object Store → ADR-067 (flat-FS + iface). Slices to update. |
| #1063 | roxabi-blobs package | KEEP | Root slice, blocks tout |
| #1064 | contracts BlobRef | KEEP | Atomic w/ voiceCLI#144 cross-repo |
| #1065 | Telegram eager-ingest | KEEP | Voice eager-DL déjà OK; reste non-voice + BlobStore routing |
| #1066 | Discord eager-ingest | KEEP | idem #1065 |
| #1067 | STT/TTS workers BlobRef | KEEP | Cross-repo voiceCLI#144; PR #1224 ne conflicte pas |
| #1068 | ops blob mount/alerts/backup | KEEP | Last slice |

**Defer status validity** : confirmé (50MB ceiling non-atteint, inline path fonctionne).
**Cross-repo** : #1067 ↔ voiceCLI#144 = highest-coordination-risk.

---

### Epic #1049 — Deprecate file-based shared state [P2 · 6 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1049 | **EPIC** — file→NATS KV+secrets | KEEP | Body: replace `container-split.md` ref → `deployment.md` |
| #1056 | keyring.key → Podman secret | KEEP | Gate the creds chain |
| #1057 | bot_secrets → Podman secrets | KEEP | Blocked by #1056. DP open: per-bot `.container` ? |
| #1058 | turns.db → NATS KV | KEEP | **Newly unblocked** (#1055 closed). DP open: encoding (msgpack/JSON/proto) |
| #1059 | message_index.db → NATS KV | **ADJUST** | Body: remove "Blocked by #1055" (closed). Also clean `storage.md:216` stale ref. |
| #1060 | Discord watch_channels via NATS | KEEP | Blocked by #1057. DP open: Option A vs B |

**Chains parallèles débloquées** :
- `#1058`, `#1059` (infra ready)
- `#1056 → #1057 → #1060` (sequential creds chain)

---

### Epic #1158 — Close CLIpool git audit [P2 · 3 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1158 | **EPIC** — CLIpool audit findings | KEEP | Check off #1151 (closed today via PR #1223) |
| #1149 | safe.directory wildcard | KEEP | Volume `idmap` requis, pas démarré |
| #1150 | propagate agent identity to git committer | KEEP | P3-low, design open (hook vs env-var) |

---

### Parent #1102 — Render-events (parent CLOSED 2026-05-15) [3 issues à fermer]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1102 | **PARENT** | CLOSED 2026-05-15 | — |
| #1133 | Telegram _on_toolcall_v2 | **DONE** | Close — `_shared_streaming_emitter.py:154` impl complète via #1214 |
| #1134 | Discord _on_toolcall_v2 + STREAM_ARGS | **DONE** (parity) | Close parity path. Optionally re-open `LYRA_DISCORD_TOOLCALL_STREAM_ARGS` opt-in si toujours voulu |
| #1135 | multi-orphan ToolCallEnd ordering | **DONE** | Close — couvert par `test_stream_processor.py:1352` + `:504` (3-orphan) |

---

### Orphans (no parent) — Streaming/LLM/NATS [10 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1225 | STTNoiseError shadow (PR #1224 follow-up) | KEEP | S — 1 import change |
| #1219 | sanitize `str(exc)` in WorkerError.message | KEEP | S — defense-in-depth |
| #1216 | rewrite 18 v1-skipped tests | KEEP | F-lite, parallélisable par classe |
| #1201 | stream() true async generator | KEEP | F-lite, soak post-#1197 |
| #1200 | worker_error truncation test | KEEP | S — single parametrized test |
| #1199 | runtime KNOWN_CODES guard | KEEP | S — `_make_worker_error` factory |
| #1198 | backend-enum cleanup tail | **ADJUST** | Retitle "3" → "5" follow-ups (body lists 5) |
| #1147 | ACL llmCLI worker JS.API.STREAM.INFO denial | KEEP | F-lite cross-repo, option B (skip_hub_readiness flag) |
| #1113 | RunErrorRenderEvent.code taxonomy | **STALE** | Close — pas de consumer (dashboard/AG-UI absent). Re-open quand consumer existe |
| #1082 | helper-side NATS publish for MintFailureEvent | KEEP | F-lite — DP: dédié `lyra-gh.seed` vs widen clipool seed |

**Mini-cluster epic candidate** : #1199 + #1200 + #1219 + #1198 = "code-quality cleanup post-#1119" (4× S, theme cohérent).

---

### Orphans (no parent) — Infra/CI/Architecture [8 issues]

| # | Titre court | Status | Action |
|---|---|--------|--------|
| #1194 | decide: async drain pipeline | KEEP | **DP owner** — A/B/C decision. Reco: B (kill — drain refs sont dead weight) |
| #1152 | redirect 33 ADR refs to canonical | **STALE** | XS sed-work, P3, oublié 2 mois. Close or revised scope (exclude quadlet rationale comments) |
| #1124 | rotate-* script hardening | **ADJUST** | OS-3 done (PR #1118 commit `a4a37f6c`). Reste: OS-1 decision, OS-2 (chmod race), OS-4 (`tr -d '\n'` consistency) |
| #1095 | slim non-clipool services (~5GB) | **STALE** | Out-of-repo (roxabi-container). Park backlog. |
| #1093 | isolate test fakes (tests/fakes/ + importlinter) | KEEP | Root cause 2026-05-06 incident toujours non-fixé |
| #1091 | roundtrip CI factory-acl genkeys | KEEP | **P1-high**, nats-server already in CI, just wire workflow |
| #1085 | lyra-monitor systemctl is-active | **ADJUST** | 60% done : check_process wired. Reste: clipool+nats in service_names, restart-loop detection, journal-tail in alerts |
| #1079 | Extract TurnStoreProtocol | KEEP | 6 `ignore_imports` debt entries dans `.importlinter` confirment violation live |

---

## Actions prioritisées

### A. Close immédiat (3 issues, ~5 min)
- `gh issue close 1133 -c "Done via #1214 + #1192. Shared base _shared_streaming_emitter.py:154 provides default recap."`
- `gh issue close 1134 -c "Parity done via #1214. Open follow-up if LYRA_DISCORD_TOOLCALL_STREAM_ARGS opt-in still wanted."`
- `gh issue close 1135 -c "Coverage in test_stream_processor.py:1352 + :504. Test-debt resolved."`

### B. Body adjustments (7 issues, sed-pass)
- #1048 — `NatsLlmDriver` → `NatsLlmClient`
- #1059 — remove "Blocked by #1055"
- #1061 (epic) — NATS Object Store → ADR-067 flat-FS strategy
- #1085 — mark check_process landed; residual list
- #1124 — mark OS-3 done
- #1198 — retitle 3 → 5 follow-ups
- #1204 — mark #1142 closed; clarify #1046 dependency

### C. Decisions humaines requises (3 DPs ouverts)
- #1194 — async drain pipeline : kill (B) | scope (A) | defer (C)
- #1057 — per-bot `.container` vs single+multi-Secret
- #1060 — Option A (publish-at-boot) vs B (KV bucket `lyra-bot-settings`)
- #1082 — `lyra-gh.seed` dédié vs widen clipool seed (ADR-046)

### D. STALE → close as not planned (2 issues)
- #1113 — `RunErrorRenderEvent.code` taxonomy : pas de consumer → re-open quand dashboard/AG-UI démarre
- #1095 — slim non-clipool images : work hors-repo → park backlog

### E. Hot button — production bug
- #1052 — `vault.add_from_url` composite **fixe `[scraping unavailable]` en prod**, mais bloqué 4 niveaux dans epic #1044 (chain #1203→#1046→#1047→{#1050,#1051,#1048}→#1052). Considérer un workaround court-terme.

### F. Newly unblocked (peuvent démarrer maintenant)
- #1058, #1059 — JetStream prereq #1055 closed
- #1091 — P1-high, infrastructure (nats-server) déjà en CI

## Milestone candidates

| Milestone | Issues | Rationale |
|-----------|--------|-----------|
| **Worker fleet v1** (M-?, P1) | #1044 + 10 subs | Production bug fix + arch milestone |
| **State migration** (M3-adjacent ou M4-pre?) | #1049 + 5 subs | NATS KV consolidation |
| **BlobStore** (parked) | #1061 + 6 subs | Defer P2 until usage signals |
| **Code-quality cleanup post-#1119** (new mini-epic) | #1198, #1199, #1200, #1219 | 4× S, theme cohérent |
| **Infra hardening** (mini) | #1091, #1093, #1124 | CI roundtrip + test isolation + script hardening |
