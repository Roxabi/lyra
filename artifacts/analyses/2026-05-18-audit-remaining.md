---
title: Audit 2026-05-18 — Findings restants à instruire
status: open
parent: 2026-05-18-codebase-audit.md
updated: 2026-05-19
---

# Audit 2026-05-18 — Reste à faire

Snapshot post-implémentation. Source de vérité pour ce qui n'a pas été tranché.

## Contexte

- **Audit origine** : `artifacts/analyses/2026-05-18-codebase-audit.md` (livrable principal, 7 clusters, 26 findings)
- **Sub-livrables** : `audit-2026-05-18/{01-cartography, 02-hexagonal, 03-mutualisation, 04-simplification}.md`
- **Sessions d'implémentation** : 2026-05-18 (2 sessions consécutives, 7 PRs)

## Implémenté (mergé)

| PR | Cluster | Impact |
|---|---|---|
| #1226 | 1 — dead code phase 1 (PR #666 héritage) | −56 LOC |
| #1233 | 1 — dead code phase 2 (`edit_trace`) | −68 LOC |
| #1237 | 1.5 + 2 — docs taxonomy initiale + `lyra.obs` scaffolding doc | docs |
| #1238 | 3.1 + 2.4 — hex hygiene (pairing DI Pool + `AuditSink` → `core/ports/`) | refactor |
| #1239 | 2 — renames (`config/` → `data/`, `tests/integrations` → `tests/integration/integrations`) | rename |

## En attente de review (open)

| PR | Cluster | Note |
|---|---|---|
| [#1240](https://github.com/Roxabi/lyra/pull/1240) | 2.2 — rename narrow `AgentStoreProtocol` → `AgentSeederTarget` | Mini-DP #2 (role interface naming) |
| [#1241](https://github.com/Roxabi/lyra/pull/1241) | DP #1 — docs(core) taxonomy canon | Follow-up #1237, remplace wording maison par Cockburn/Fowler |

## Issues en vol (autres sessions)

| # | Sujet |
|---|---|
| [#1235](https://github.com/Roxabi/lyra/issues/1235) | Mutualiser heartbeat NATS ×4 (cluster 4) |

## Restant à instruire (audit, ¬urgent)

| Ref | Sujet | Effort | Note post-investigation |
|---|---|---|---|
| 2.3 | `SessionToolsProtocol` localisation | F-lite | **À re-classifier** sous nouvelle taxonomie (Cockburn/Fowler, PR #1241) AVANT décision : si capability externe → `core/ports/` ; si collaboration interne → role interface inline avec sub-domain consommateur |
| 4.3 | `_parse_*_timeout()` consolidation | X-lite | Helper utility, plusieurs callsites — extraction vers helper commun |
| 4.4 | `_DEFAULT_NATS_URL` ×2 | quick win S | Const dédupliquée — 1 fichier source |
| 5.1 | `nats_llm_client._build_request()` extraction | F-lite | **À débattre** : contradiction A1/A2 dans audit (A1 = extract vers `lyra/llm/llm_request_builder.py`, A2 = inline). Arbitrage audit = extract, mais user à valider |
| 6.1 | 5 stores sans Protocol | F-lite | Déjà tracké `DEBT:importlinter-adr048-transition` — couvert par dette existante, ¬ nouveau ticket |
| 7.1 | `render_event_codec` exemption | S | Dépend de S4 #1192 (review-fix strategy) — bloqué jusqu'à résolution amont |

## Dette long terme (documentée, déclenchement conditionnel)

| Ref | Sujet | Doc location | Trigger |
|---|---|---|---|
| LT-1 | "Orthodoxie pure" — split `ChannelAdapter` → `MessageReceiver` (inbound/driver port) + `MessageSender` (outbound/driven port) sous `core/ports/inbound/` + `core/ports/outbound/` | `src/lyra/core/CLAUDE.md` § "Future orthodoxie pure" (via PR #1241) | Channel qui n'implémente qu'une direction OU friction ISP testabilité OU 4ème channel ajouté |

## Faux positifs identifiés et arbitrés

| Ref | Audit initial | Verdict | Référence canon |
|---|---|---|---|
| 2.1 | `ChannelAdapter` à migrer vers `core/ports/` | **Faux positif** — role interface (Fowler), pas driven port. Co-located avec hub sub-domain. | [[Cockburn]] + [[Fowler RoleInterface]] (PR #1241 docstrings + core/CLAUDE.md) |
| 2.2 | `AgentStoreProtocol` narrow ≡ duplicate du full | **Faux positif** — role interface narrow (ISP). Renommée `AgentSeederTarget` pour casser homonymie. | PR #1240 |

## Décisions architecturales prises pendant la mise en œuvre

| Décision | Implémenté dans |
|---|---|
| `core/ports/` = driven (secondary) ports (Cockburn) uniquement | PR #1241 |
| Role interfaces (Fowler) = co-located avec sub-domain ; pas migrés vers `core/ports/` | PR #1241 (docstrings hub_protocol, middleware, ports/__init__) |
| `lyra.obs` est scaffolding (roadmap Langfuse), ¬dead code | PR #1237 (`src/lyra/obs/CLAUDE.md`) |
| Role-interface naming = collaboration ¬ supplier (`AgentSeederTarget` ¬ `AgentStoreProtocol`) | PR #1240 |
| Future ortho pure = split `ChannelAdapter` en 2 ports canoniques | docs(core) PR #1241 |

## TODO mineur

- [ ] Mettre à jour `2026-05-18-codebase-audit.md` (livrable principal) pour ajouter section "Fausses pistes" listant 2.1 + 2.2. Cosmétique, ¬bloquant.

## Lien retour

→ `2026-05-18-codebase-audit.md` (livrable principal) — pour les findings initiaux pleins.
