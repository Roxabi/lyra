# Audit codebase Lyra — 2026-05-18

> Audit transversal sur 3 dimensions (hexagonal, mutualisation, simplification).
> Scope: `src/lyra/` + `packages/roxabi-*` (+ `tests/` pour A2/A3).
> Livrables intermédiaires dans `audit-2026-05-18/`.

---

## 1. Exec summary

Architecture globalement saine : 0 violations importlinter, layers propres, packages `roxabi-nats`/`roxabi-contracts` bien isolés. La dette identifiée est localisée, non systémique.

Trois chantiers majeurs :

- **Dead code post-#666** — cluster `smart_routing` + `lyra.obs` + shims orphelins représente ~350 LOC à supprimer sans risque. Chantier le plus actionnable immédiatement.
- **Ports implicites dispersés** — `ChannelAdapter`, `SmartRoutingProtocol`, `AgentStoreProtocol` (dupliqué), `SessionToolsProtocol` (manquant) vivent hors `core/ports/`, rendant la surface hexagonale difficile à naviguer. Chantier structurel, effort M.
- **Boilerplate NATS ×4 non mutualisé** — heartbeat lifecycle + `_on_heartbeat` body copié verbatim dans les 4 clients NATS. Extraction `NatsWorkerClientBase` dans `roxabi-nats` est le seul candidat réel à extraction de package.

---

## 2. Quick wins (≤1h, zéro risque)

1. **Supprimer `smart_routing_protocol.py`** — `core/smart_routing_protocol.py:1-45` — 0 callers, 0 tests directs. Supprimer en même PR que #3.
2. **Supprimer `llm/smart_routing.py`** — `llm/smart_routing.py:1-5` — 5 lignes de docstring, 0 callers. Même PR que #1.
3. **Supprimer `llm/errors.py`** — `llm/errors.py:1-6` — shim 6 lignes, A2 + A3 confirment 0 callers réels. Vérifier `grep -rn "from lyra.llm.errors"` avant merge.
4. **Retirer `PlatformCallbacks.edit_trace`** — `adapters/shared/_shared_streaming_emitter.py:78-80` — champ vestigial post-#1214, impact ≤3 fichiers adapters.
5. **Supprimer `AgentStoreProtocol` local** dans `core/agent/agent_seeder.py:21` — doublon de `core/stores/agent_store_protocol.py`, remplacer par import du canonical.
6. **Documenter `A1#5`** — ajouter commentaire dans `core/hub/middleware/middleware.py` que `PipelineMiddleware` est un port interne hub intentionnel (scope justifié, ¬déplacer vers `core/ports/`).
7. **Documenter `A1#9`** — ajouter entrée dans `agent_cmd/CLAUDE.md` que `agent_cmd → core` est intentionnel (couche CLI applicative).
8. **Ajouter `agent_cmd/` dans tableau CLAUDE.md racine** — édition documentation pure, 0 risque.
9. **Renommer `src/lyra/config/` en `src/lyra/data/`** — 1-2 sites de chargement TOML à mettre à jour, élimine la collision nominale avec `config.py`.
10. **Harmoniser `tests/integrations/` → `tests/integration/`** — mv + mise à jour `pyproject.toml` testpaths.

---

## 3. Findings consolidés

### Cluster 1 — Dead code & shims post-#666

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 1.1 | `smart_routing_protocol.py` orphelin | [A1#4](audit-2026-05-18/02-hexagonal.md), [A3#1](audit-2026-05-18/04-simplification.md) | S | fort | hors registry | quick win |
| 1.2 | `llm/smart_routing.py` shim vide | [A3#3](audit-2026-05-18/04-simplification.md) | S | moyen | hors registry | quick win |
| 1.3 | `llm/errors.py` shim 6 lignes | [A2#8](audit-2026-05-18/03-mutualisation.md), [A3#4](audit-2026-05-18/04-simplification.md) | S | moyen | hors registry | quick win |
| 1.4 | `PlatformCallbacks.edit_trace` vestigial | [A3#5](audit-2026-05-18/04-simplification.md), A2 additionnel | S | moyen | hors registry | quick win |
| 1.5 | `lyra.obs` sans consommateur runtime (199 LOC) | [A3#2](audit-2026-05-18/04-simplification.md) | S | fort | hors registry | issue S-lite (décision roadmap) |

Findings 1.1–1.4 : PR atomique unique. 1.5 nécessite une décision roadmap (Langfuse oui/non) avant action.

### Cluster 2 — Ports implicites dispersés hors `core/ports/`

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 2.1 | `ChannelAdapter` port dans `hub_protocol.py` | [A1#2](audit-2026-05-18/02-hexagonal.md) | M | fort | hors registry | issue F-lite |
| 2.2 | `AgentStoreProtocol` dupliqué | [A1#3](audit-2026-05-18/02-hexagonal.md), A3 additionnel | S | moyen | hors registry | quick win |
| 2.3 | `SessionToolsProtocol` manquant dans `core/ports/` | [A1#7](audit-2026-05-18/02-hexagonal.md) | M | moyen | `importlinter-shared-modules-transitive` | issue F-lite (ADR-061) |
| 2.4 | `AuditSink` port hors `core/ports/` | A1 additionnel | S | faible | hors registry | issue X-lite |
| 2.5 | `core/trace.py:TraceLogRecord` hors `core/ports/` | A1 additionnel | S | faible | hors registry | à débattre |

2.1 et 2.3 sont des issues distinctes (boundaries différentes). 2.2 est un quick win direct.

### Cluster 3 — Violation hexagonale runtime `core → infrastructure`

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 3.1 | `pairing_protocol.py` import runtime infra (seule vraie violation) | [A1#1](audit-2026-05-18/02-hexagonal.md) | S | fort | `importlinter-adr048-transition` | issue F-lite (drain dette existante) |

Ce finding est la seule vraie violation hexagonale runtime. Les 11 autres exemptions `core → infrastructure` sont toutes `TYPE_CHECKING` — non-violations confirmées.

### Cluster 4 — Boilerplate NATS ×4 non mutualisé

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 4.1 | Heartbeat lifecycle copié ×4 dans clients NATS | [A2#1](audit-2026-05-18/03-mutualisation.md) | M | fort | `file-exemptions` (indirect) | issue F-lite → `NatsWorkerClientBase` dans `roxabi-nats` |
| 4.2 | `_on_heartbeat` body copié ×4 | [A2#2](audit-2026-05-18/03-mutualisation.md) | M | fort | couvre par 4.1 | résoudre avec 4.1 |
| 4.3 | `_parse_*_timeout()` copié ×3 | [A2#10](audit-2026-05-18/03-mutualisation.md) | S | faible | hors registry | issue X-lite → `nats/_timeout.py` in-tree |
| 4.4 | `_DEFAULT_NATS_URL` + env resolution ×2 | [A2#3](audit-2026-05-18/03-mutualisation.md) | S | moyen | hors registry | quick win → `_resolve_nats_url()` dans `cli.py` |

4.1 + 4.2 : issue unique. 4.3 : trivial, in-tree, ¬extraction package. 4.4 : quick win sans ADR.

### Cluster 5 — `nats_llm_client.py` — mécanique mixte

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 5.1 | `_build_request` text-folding = logique applicative dans couche transport | [A1#6](audit-2026-05-18/02-hexagonal.md) | L | moyen | `file-exemptions` | à débattre (voir §4 contradiction) |

L'action dépend de la résolution de la contradiction A1/A2 (§4). En attente de décision.

### Cluster 6 — Stores sans Protocol (ratio 4:10)

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 6.1 | 5 stores infra sans protocol `core/stores/` correspondant | [A2#6](audit-2026-05-18/03-mutualisation.md), A1 transmis | M | moyen | `importlinter-adr048-transition` | tracké — confirmer couverture dans plan de drain |

Ce finding est déjà dans la dette ouverte. Pas de nouvelle issue — vérifier que les 5 stores manquants sont listés dans le plan de drain ADR-048.

### Cluster 7 — Anomalies structurelles & exemptions expirantes

| # | Finding | Sources | Effort | Impact | Status debt | Action |
|---|---------|---------|--------|--------|-------------|--------|
| 7.1 | `render_event_codec.py` exemption à expirer (S4 deferred) | [A3#8](audit-2026-05-18/04-simplification.md) | M | moyen | `file-exemptions` | issue X-lite si S4 (#1192) n'est pas ouvert |
| 7.2 | `config/` TOML vs `config.py` Python — collision nominale | [A3#9](audit-2026-05-18/04-simplification.md) | S | faible | hors registry | quick win |
| 7.3 | `tests/integration/` vs `tests/integrations/` incohérence | [A3#10](audit-2026-05-18/04-simplification.md) | S | faible | hors registry | quick win |
| 7.4 | `obs/` absent CLAUDE.md racine + hors contrat | A1, A3 | S | faible | hors registry | résoudre avec 1.5 |
| 7.5 | `monitoring/__main__.py → core.logging_setup` | [A1#8](audit-2026-05-18/02-hexagonal.md) | S | faible | hors registry | à débattre (long terme → `obs/`) |

---

## 4. Contradictions arbitrées

### Contradiction 1 — `nats_llm_client.py` : extraire ou garder in-tree ?

- **A1#6** : `_build_request()` + text-folding = logique applicative qui devrait sortir de la couche transport ; recommande extraction vers `core/` ou `llm/`. Effort L.
- **A2#4** : `NatsLlmClient` implémente `LlmProvider` protocol (logique applicative) ET gestion transport — la règle text-folding est un invariant de contrat LLM spécifique à Lyra ; ¬extraire dans `roxabi-nats` ; garder in-tree.
- **Résolution proposée** : A2 a raison sur la cible (¬`roxabi-nats` — violer ADR-047 Rule 1 "zero lyra.* subject knowledge"). A1 a raison sur le diagnostic (mélange sémantique). Compromis : extraire `_build_request()` vers `src/lyra/llm/llm_request_builder.py` (in-tree, couche `llm/`), sans toucher au transport NATS. Ceci résout le mélange sémantique sans extraction de package. Effort réduit à M. Classer comme issue F-lite, pas urgente.

---

## 5. Matrice priorisation

| Effort \ Impact | Faible | Moyen | Fort |
|----------------|--------|-------|------|
| **S** | 7.2 ; 7.3 ; 2.5 ; 7.4 | 1.3 ; 1.4 ; 4.4 ; 2.2 ; 2.4 | 1.1 ; 1.2 ; 3.1 |
| **M** | 4.3 | 2.3 ; 6.1 ; 7.1 ; 5.1 | 2.1 ; 4.1+4.2 |
| **L** | — | — | 1.5 (décision) |

Lecture : colonne "Fort" + ligne "S" → actions immédiates. Ligne "M" + colonne "Fort" → issues sprint suivant.

---

## 6. Relation avec `artifacts/debt/`

### Findings déjà trackés dans un slug existant

| Finding | Slug debt | Notes |
|---------|-----------|-------|
| 3.1 `pairing_protocol` import runtime | `importlinter-adr048-transition` | La vraie violation — drain #1163 |
| 2.3 `SessionToolsProtocol` manquant | `importlinter-shared-modules-transitive` | ADR-061 non exécuté |
| 6.1 stores sans protocol ×5 | `importlinter-adr048-transition` | Confirmer que les 5 stores sont dans le plan |
| 7.1 `render_event_codec` exemption | `file-exemptions` | S4 déferred — slug suit |

### Findings candidats nouveaux slugs

- `dead-code-smart-routing` — `smart_routing_protocol.py` + `llm/smart_routing.py` + `llm/errors.py` + `PlatformCallbacks.edit_trace` (cluster 1.1–1.4, groupable)
- `ports-implicit-core` — `ChannelAdapter` + `AuditSink` hors `core/ports/` (findings 2.1, 2.4)
- `nats-worker-client-base` — heartbeat ×4 (findings 4.1–4.2)

### Findings hors scope du registry (architecture, structure)

- 1.5 `lyra.obs` sans consommateur — décision roadmap, pas un slug lint/gate
- 2.5 `TraceLogRecord` hors `core/ports/` — faible impact, ¬gate-able
- 4.4 `_DEFAULT_NATS_URL` ×2 — trivial, inline fix
- 5.1 `nats_llm_client` mélange sémantique — architecture, non gate-able
- 7.2–7.5 anomalies structurelles — renames/docs, hors scope quality gates

---

## 7. Prochaines actions suggérées

- Créer PR "dead code cluster #666" — `smart_routing_protocol.py` + `llm/smart_routing.py` + `llm/errors.py` + `edit_trace` — 0 risque, libère ~350 LOC
- Créer issue F-lite `ChannelAdapter → core/ports/` — cluster 2.1 — améliore discoverabilité hexagonale + alignement avec `LlmProvider`/`TtsProtocol`
- Créer issue F-lite `NatsWorkerClientBase` dans `roxabi-nats` — cluster 4.1+4.2 — extraction légère, fort impact maintenabilité
- Créer issue F-lite `pairing_protocol` → bootstrap DI — cluster 3.1 — drain de la seule vraie violation hexagonale runtime
- Décider statut `lyra.obs` (Langfuse roadmap oui/non) — cluster 1.5 — débloquer suppression ou migration `packages/roxabi-obs`
- Vérifier que S4 `render_event_codec` (#1192) est ouvert — cluster 7.1 — sinon créer
- Confirmer les 5 stores manquants sont dans le plan de drain `importlinter-adr048-transition` — cluster 6.1
- Appliquer quick wins structurels (9 + 10) dans PR docs — `config/` → `data/`, `tests/integrations/` → `tests/integration/`, CLAUDE.md `agent_cmd/`

---

## 8. Fausses pistes consolidées

- **`render_event_codec.py` vs `cli_streaming_parser.py`** — invariants orthogonaux (NATS registry-driven vs NDJSON CLI stateful). ¬factoriser. (A2 §Fausses pistes)
- **`send_with_retry` vs retry `_dispatch.py`** — sémantiques divergentes (échec silencieux vs circuit breaker conditionnel). ¬factoriser. (A2 §Fausses pistes)
- **`core/stores/` ratio 4:10** — migration en cours, pas duplication. (A2)
- **11 exemptions TYPE_CHECKING `core → infrastructure`** — toutes sous `if TYPE_CHECKING:`, 0 import runtime. Non-violations. (A1 §Faux positifs)
- **`processor_registry → integrations` transitive** — exemption correcte, `integrations/base.py` n'importe que stdlib. (A1)
- **`agent_cmd → core`** — CLI applicative au-dessus du domaine, architecturalement correct. (A1)
- **`OutboundListener` dans `adapters/shared/`** — port adapters-interne légitime, ne monte pas vers `core`. (A1)
- **`JetStreamAuditSink`** — câblé dans `hub_standalone.py` + `wiring_helpers.py`, tests complets, actif en prod. (A3)
- **6 fichiers CLI racine `cli_*.py`** — pattern Typer modulaire intentionnel, chacun est une app distincte. (A3)
- **`wiring_helpers.py` God module** — 10 fonctions focalisées, 1 caller intentionnel (ADR-059/V10). (A3)
- **`Bus[T]` Protocol** — 2 implémentations runtime réelles (`LocalBus`, `NatsBus`). Abstraction justifiée. (A3)

---

## 9. Hors scope & non vérifié

- `bootstrap/wiring/`, `bootstrap/lifecycle/`, `bootstrap/standalone/` — absence de logique métier non vérifiée
- `nats_llm_client.py:416-628` (`_stream_gen` complet) — logique détaillée non exhaustivement auditée
- `infrastructure/audit/jetstream_sink.py` — présence confirmée active, contenu interne non audité (A1)
- `core/cli/` 14 fichiers — potentiel domain leak dans `cli_pool_session.py` non audité (A1)
- `adapters/discord/discord_outbound.py` rendering — finding A2#5 basé sur inférence structurelle, non lecture directe
- `nats/nats_stt_client.py` + `nats/nats_tts_client.py` `_parse_*_timeout()` — finding A2#10 par inférence
- `packages/roxabi-nats/driver_base.py` — potentiel overlap avec finding 4.1 si base class heartbeat existe déjà
- `packages/roxabi-nats/` + `packages/roxabi-contracts/` — hors importlinter scope, conformité hexagonale interne non auditée
- `src/lyra/tools/gh_token/` — non exploré
- Status exact des 72 ADRs post-consolidation — consolidation #1145 non mergée dans cette branche
- Tests coverage maps (317 fichiers) — non cartographiés
- `core/` exemption `#858 config dataclass extraction` — si extraction réalisée, exemption peut-être obsolète
- `bootstrap/factory/hub_builder.py` vs `wiring_helpers._build_hub` — chevauchement partiel détecté, non audité

---

## 10. Statut post-implémentation (2026-05-19)

### PRs livrées

| PR | Cluster(s) couvert | Statut | Impact |
|---|---|---|---|
| [#1226](https://github.com/Roxabi/lyra/pull/1226) | 1.1 + 1.2 + 1.3 (smart_routing + llm/errors shims) | MERGED 2026-05-18 | −56 LOC |
| [#1233](https://github.com/Roxabi/lyra/pull/1233) | 1.4 `edit_trace` vestigial | MERGED 2026-05-18 | −47 LOC |
| [#1237](https://github.com/Roxabi/lyra/pull/1237) | 1.5 (doc `lyra.obs` scaffolding) + docs taxonomie initiale + CLAUDE.md `agent_cmd/`,`obs/` | MERGED 2026-05-18 | +106 docs |
| [#1238](https://github.com/Roxabi/lyra/pull/1238) | 3.1 pairing DI Pool + 2.4 `AuditSink` → `core/ports/` | MERGED 2026-05-18 | net −41 |
| [#1239](https://github.com/Roxabi/lyra/pull/1239) | 7.2 `config/`→`data/` + 7.3 `tests/integration/` | MERGED 2026-05-18 | rename |
| [#1240](https://github.com/Roxabi/lyra/pull/1240) | 2.2 rename narrow → `AgentSeederTarget` | OPEN (auto-merge enabled) | role-interface naming |
| [#1241](https://github.com/Roxabi/lyra/pull/1241) | DP #1 taxonomy canon (révision #1237) | OPEN | docs(core) + LT-1 dette |

### Issues en vol

| Issue | Cluster | Statut | Note |
|---|---|---|---|
| [#1235](https://github.com/Roxabi/lyra/issues/1235) | 4.1 + 4.2 (heartbeat NATS ×4) | OPEN, 0 commentaire | Spec détaillée (approche A/B/C, acceptance criteria) |

### Faux positifs identifiés et arbitrés pendant implémentation

- **2.1 `ChannelAdapter` à migrer vers `core/ports/`** — **faux positif**. Révisé en *role interface* (Fowler) intentionnellement co-located avec hub sub-domain. `core/ports/` est réservé aux *driven ports* (Cockburn). Référence : PR #1241 docstrings + `src/lyra/core/CLAUDE.md` § "Ports & Protocols taxonomy".
- **2.2 `AgentStoreProtocol` dupliqué** — **faux positif**. Le narrow dans `agent_seeder.py` est un *role interface* (ISP), différent du full dans `core/stores/`. Renommé `AgentSeederTarget` pour casser l'homonymie (PR #1240). Convention : role-interface nommé par sa collaboration, ¬ par son supplier.

### Décisions architecturales prises

| Décision | Implémentée dans |
|---|---|
| `core/ports/` = driven (secondary) ports (Cockburn) uniquement | PR #1241 |
| Role interfaces (Fowler) = co-located avec leur sub-domain, ¬migrés vers `core/ports/` | PR #1241 |
| `lyra.obs` est scaffolding (roadmap Langfuse/OTel), ¬dead code | PR #1237 (`src/lyra/obs/CLAUDE.md`) |
| Role-interface naming = collaboration ¬ supplier (`AgentSeederTarget` ¬ `AgentStoreProtocol`) | PR #1240 |
| **LT-1 dette** : "orthodoxie pure" — split `ChannelAdapter` → `MessageReceiver` (inbound/driver) + `MessageSender` (outbound/driven) sous `core/ports/{inbound,outbound}/` | docs(core)/CLAUDE.md (PR #1241) — déclencheur : channel mono-directionnel OU friction ISP testabilité |

### Mémoire enrichie

- `feedback_concurrent_session_state_drift` — détectée pendant impl : un fixer a poussé sur la branche d'une PR pendant qu'elle s'auto-mergeait (cherry-pick + nouvelle PR #1241 requis pour rattraper la révision orpheline).

### Reste à instruire

→ `2026-05-18-audit-remaining.md` — punch list actionnable post-implémentation.

Items qui restent (¬urgent) :

| Ref | Sujet | Note |
|---|---|---|
| 2.3 | `SessionToolsProtocol` localisation | **À re-classifier** sous nouvelle taxonomie Cockburn/Fowler avant action |
| 4.3 | `_parse_*_timeout()` consolidation in-tree | X-lite |
| 4.4 | `_DEFAULT_NATS_URL` ×2 | quick win S |
| 5.1 | `nats_llm_client._build_request()` extraction | à débattre — contradiction A1/A2 arbitrée vers extraction |
| 6.1 | 5 stores sans Protocol | déjà tracké `DEBT:importlinter-adr048-transition` |
| 7.1 | `render_event_codec` exemption | dépend S4 #1192 |
