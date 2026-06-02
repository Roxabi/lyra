# Epic #1662 — Execution Ledger (model B: sequential-piloted + bounded parallel)

SSoT = native GitHub `blocked_by` (verified 2026-06-02), **reinterpreted** to strip parent↔child hierarchy noise.
Execution unit = **LEAVES** (umbrella parents 1634/1638/1640 dropped → auto-close when children merge).

Pipeline per issue: ω worktree → `/dev #N` (autonomous, no gates, symbolic rules injected) → `/code-review`
→ `/fix` → PR (doc: RC + Corr∈{Patch⊻Archi} + L) → CI green(ci∧trufflehog) ∧ label `reviewed`
→ `gh pr merge --merge --auto` → `/ci-watch` until MERGED → unblock dependents.

Parallelism: AUTHORIZED on disjoint footprints, **cap = 3 concurrent /dev**.

## Dropped (umbrella parents — auto-close on children merge)
1634 (=1660⊕1663⊕1664⊕1665) · 1638 (=1666⊕1667) · 1640 (≈1661, +tts_protocol.py folded into 1661)

## Reinterpreted dependency graph (real technical deps only)
```
1659 → 1635   (configs created before call-sites wired)   ⚠️ native records INVERSE (1659←1635) = bogus
1660 → 1666   (refactor adapters/shared before extracting helpers out of it)
1661 → 1667   (.importlinter writer ordering)
1635 → 1654   (constants extracted before the gate that bans them)  ✓ native
1652,1653,1654,1655 → 1656  (template references the checks)         ✓ native
1657 → 1658   (shared CLAUDE.md)
```

## Wave schedule (≤3 concurrent; merge-gate between waves where deps cross)

| Wave | issues | size | parallel-safe rationale |
|------|--------|------|--------------------------|
| **W1** | 1636 1637 1639 1659 1660 1661 1663 1664 1665 | S/F-lite | footprints disjoint (core/pool · infra/stores · bootstrap/{standalone,infra,factory} · core/config · adapters · core/{ports,stores,auth} · bootstrap/wiring · bootstrap/factory/voice_overlay · agent_cmd) |
| **W2** | 1635(←1659) 1666(←1660) 1667(←1661) | F-lite/F-lite/S | each waits its W1 blocker merged; mutual footprints disjoint (core call-sites · inbound+shared · inbound/wire_parser+.importlinter) |
| **W3** (track CI, serial) | 1652→1653→1655→1654(←1635)→1656 | S | all share `.pre-commit-config.yaml`+`ci.yml` → strictly serial |
| **W4** (track docs, serial) | 1657→1658 | S | share CLAUDE.md → serial; runs concurrent with W3 |

W3+W4 may start any time (only 1654←1635 gates inside W3); scheduled after code waves to bound concurrency.

## Status ledger
| issue | wave | size | blockers (MERGED-gate) | status |
|-------|------|------|------------------------|--------|
| 1636 | W1 | Archi | — | ✅ #1687 merged (fix-forward; 276 SLOC, 29-line orchestrator) |
| 1637 | W1 | F-lite(P0) | — | ✅ #1675 merged |
| 1639 | W1 | F-lite | — | ✅ #1686 merged (re-dev wf_5bd2946f) |
| 1659 | W1 | F-lite | — | ✅ #1679 merged |
| 1660 | W1 | F-lite | — | ✅ #1681 merged |
| 1661 | W1 | F-lite | — | ✅ #1680 merged (snapshot fixed: dbecd9c8) |
| 1663 | W1 | F-lite | — | ✅ #1684 merged |
| 1664 | W1 | S | — | ✅ #1683 merged |
| 1665 | W1 | S | — | ✅ #1682 merged |
| 1635 | W2 | F-lite | 1659✅ | ✅ #1689 merged |
| 1666 | W2 | Archi | 1660✅ | ✅ #1690 merged (relocate→core) |
| 1667 | W2 | S | 1661✅ | ✅ #1688 merged (conflict reconciled a2fcffac) |
| 1652 | W3 | S | — | ✅ #1693 merged (ci.yml YAML repaired ef3724f0) |
| 1653 | W3 | M | 1652✅ | ✅ #1695 merged (gate+45 annotations; multi-line/docstring-aware) |
| 1655 | W3 | M | 1653✅ | ✅ #1696 merged (conflict reconciled 142105e4; file_exemptions.txt = 0 active entries post-SLOC-switch → gate forward-blocking only, no baseline/sibling needed) |
| 1654 | W3 | M/L | 1655✅, 1635✅ | ✅ #1697 merged (agent aef98231 branched off STALE local staging → never wired ci.yml/pre-commit; I reconciled fresh staging + finished wiring + 3 gates; baseline=86 sigs, qg.conf drift clean) |
| 1656 | W3 | S | 1654✅ | ✅ #1699 merged (last leaf — PR template w/ quality-hygiene checklist) |
| 1698 | — | F-lite | 1654 | ⬜ sibling: burn down 86 grandfathered constants → Config/Protocol (parent=1662, blocked-by 1654) |
| 1657 | W4 | S | — | ✅ #1692 merged |
| 1658 | W4 | S | 1657✅ | ✅ #1694 merged |

*1637 flagged S→F-lite pending confirmation.
Legend: ⬜ pending · 🔨 dev · 🔍 review/fix · 🟡 PR/CI · ✅ merged · ⛔ blocked/failed
Dropped→auto-close: 1634 1638 1640.

## FINAL — 19/19 leaves merged (2026-06-02)
All 19 dev-leaves merged. Umbrellas #1634 #1638 #1640 closed (all children done). Epic #1662 has 1 open child remaining = #1698 (deliberate baseline burn-down carry-forward, F-lite/Low, blocked-by 1654). Worktrees + branches pruned (only unrelated #1674 remains). Local staging ff'd to dd22dc75.
W3 PRs: #1693(1652) #1695(1653) #1696(1655) #1697(1654) #1699(1656). W4: #1692(1657) #1694(1658).
Notable saves: #1696/#1697 stack.yml conflict reconciles (kept all 3 CI gates); #1697 agent stale-base recovery (finished ci.yml/pre-commit wiring by hand).

## Run log
- W1 run#1 `wf_5df2dba3` → **9/9 failed**. RC = transient `API 529 Overloaded` on every agent's first inference (`model:<synthetic>`, 0 tokens). NOT a design flaw — dispatch was correct. Symptom "completed without calling StructuredOutput" masked the 529.
  - Corr: Patch=re-run · Archi=`tryAgent()` retry wrapper (3 tries, 20s×attempt backoff) at inference level L + branch `-b`→`-B` (retry-safe local reset). No partial state leaked (no branches/PRs).
- W1 run#2 `wf_1235ae74` → in flight (batch1 real work confirmed: sonnet-4-6, Bash tool-use, 0×529).

## Injected rules (∀ issue, ∀ agent — verbatim into /dev, /code-review, PR body)
```
P := problème · obs(P),Sym(P) := observable,symptômes · RC(P) := root cause(s)
Corr(P) := {corrections} · Patch ⊂ Corr (local) · Archi ⊂ Corr (structurel) · Patch ∩ Archi = ∅
T := fichier cible · F := fix · I := input · trust(I) := confiance · C := code · L := niveau logique
1. RCA      : dériver RC(P). ¬fix sur obs/Sym. symptôme ≠ cause.
2. Corr     : énumérer; classer chaque ∈ Patch ⊻ Archi; CHOISIR explicitement (¬patch déguisé en archi).
3. fix@L    : appliquer F au niveau L = niveau(RC), ¬ au niveau où Sym apparaît.
4. trust(I) : ∀ I évaluer trust(I) AVANT usage; trust bas → valider/sanitize.
5. ✓        : tested(C) ∧ correct(C). vert ≠ correct. exiger les deux.
6. Doc(PR)  : tracer RC(P) + Corr choisie + classe(Patch|Archi) + niveau L.
```
