# 01 — Cartographie codebase Lyra (entrée d'audit 2026-05-18)

## 1. Module map

### src/lyra/ — modules principaux

| Module | Rôle (1 ligne) | LOC approx | Sous-CLAUDE.md ? | Notes |
|--------|----------------|------------|-----------------|-------|
| `adapters/` | Adaptateurs canaux (Telegram, Discord, CLI, NATS) | 6 892 | oui | 4 sous-modules + shared |
| `agent_cmd/` | Commandes CLI `lyra agent` (init, edit, list) | 520 | **non** | Absent du tableau CLAUDE.md racine |
| `agents/` | Implémentations agents (`SimpleAgent`) | 433 | oui | 3 fichiers dont simple_agent.py (316 LOC, exempté) |
| `bootstrap/` | Composition root, DI, wiring, lifecycle | 3 908 | oui | 5 sous-modules; factory/ = 1 596 LOC |
| `commands/` | Plugins commandes (slash commands) | 408 | oui | — |
| `config/` | Fichiers de données TOML (messages, patterns) | — (¬.py) | **non** | Répertoire data, pas un module Python |
| `core/` | Domaine central — hub, pool, agents, messaging | 15 824 | oui | 14 sous-modules; le plus gros module |
| `infrastructure/` | Stores SQLite (ADR-048), audit jetstream | 2 705 | oui | stores/ = 15 fichiers (exempté) |
| `integrations/` | Couche externe — supervisor, systemctl, vault-cli | 546 | oui | Listed as "shared floating" dans importlinter |
| `llm/` | Drivers LLM (CLI, NATS), registry, smart routing | 579 | oui | drivers/cli_nats.py = 278 LOC |
| `monitoring/` | Health-check standalone (`python -m lyra.monitoring`) | 961 | oui | — |
| `nats/` | Clients NATS (LLM, TTS, STT, image), codec | 2 754 | oui | 14 fichiers (exempté #1221); nats_llm_client.py = 628 LOC |
| `obs/` | Observability (base, noop) | 199 | **non** | Listed comme shared floating dans importlinter |
| `tools/` | gh_token helper | 713 | oui | Distinct de `tools/` racine |

**Modules racine (src/lyra/*.py) :**

| Fichier | Rôle | LOC |
|---------|------|-----|
| `cli.py` | Entry point Typer principal | 291 |
| `cli_agent.py` | CLI `lyra agent` (glue) | 88 |
| `cli_agent_create.py` | Création d'agent | 246 |
| `cli_bot.py` | Commandes bot | 119 |
| `cli_ops.py` | Vérification NATS ACL opérationnelle | 298 |
| `cli_setup.py` | Setup initial | 159 |
| `cli_voice_smoke.py` | Smoke test TTS→STT NATS round-trip | 299 |
| `config.py` | Dataclass de configuration principale | 218 |
| `errors.py` | Hiérarchie d'exceptions racine | 93 |
| `ops_audit.py` | Audit statique acl-matrix.json | 62 |

### packages/

| Module | Rôle (1 ligne) | LOC approx | Sous-CLAUDE.md ? | Notes |
|--------|----------------|------------|-----------------|-------|
| `roxabi-contracts/` | Schémas de contrats NATS partagés (ADR-049) | 1 712 | oui | voice, image, llm, jobs, gh, cli, audit |
| `roxabi-nats/` | SDK transport NATS réutilisable (ADR-045) | 2 249 | oui | adapter_base, driver_base, connect, readiness |

---

## 2. État des couches & import graph

### Contracts importlinter actifs (7)

| Nom | Type | Règle en 1 ligne |
|-----|------|-----------------|
| `clean-architecture-layers` | layers | `core ← llm/nats ← infrastructure ← adapters ← bootstrap` |
| `core-stores-no-sqlite` | forbidden | `core/stores` ne doit pas importer `aiosqlite`/`sqlite3` |
| `commands-no-infra` | forbidden | `commands` ne doit pas importer `infrastructure` directement |
| `agents-no-bootstrap` | forbidden | `agents` ne doit pas importer `bootstrap` (composition root) |
| `shared-modules-independence` | independence | `obs / errors / config / integrations / monitoring / agent_cmd` sont pair-isolés |
| `bootstrap-types-no-subpackages` | forbidden | `bootstrap/types.py` neutre, n'importe pas les sous-packages bootstrap |
| `shared-modules-upper-boundary` | forbidden | Modules shared ne remontent pas vers `bootstrap / adapters / infrastructure` |

### Violations actuelles

`uv run lint-imports` → **0 contracts broken** (7 kept, 364 files analysés, 2 168 dépendances).

### Exemptions documentées (`.importlinter`)

| Exemption | Justification (slug dette) |
|-----------|---------------------------|
| 11 imports `core → infrastructure.stores` | ADR-048 transition TYPE_CHECKING — `DEBT:importlinter-adr048-transition` |
| `core.stores.pairing_protocol → infrastructure.stores.pairing` | Façade ADR-059 V3 pending — `DEBT:importlinter-adr048-transition` |
| `agents.simple_agent → infrastructure.stores.agent_store` | TYPE_CHECKING, extraction protocol ADR-059 V9 — `DEBT:importlinter-adr048-transition` |
| `core.processors.processor_registry → integrations.base` | Chemin transitif (floating → core), pas un couplage pair — `DEBT:importlinter-shared-modules-transitive` |

---

## 3. État des gates qualité

### file_length (max=300)

- **13 fichiers exemptés** (`tools/file_exemptions.txt`)
- Top 5 plus gros (LOC déclarés) :

| Fichier | LOC | Raison (résumé) |
|---------|-----|-----------------|
| `nats/nats_llm_client.py` | 628 | LlmProvider conformance + erreurs + sanitization (#1119, #1212) |
| `core/processors/stream_processor.py` | ~549 | WorkerError + Run lifecycle + Reasoning (#1016, #1098, #1101) |
| `adapters/shared/_shared_streaming_emitter.py` | 465 | Run lifecycle + PlatformCallbacks + trace helper (#1098, #1101, #1214) |
| `bootstrap/factory/wiring_helpers.py` | 410 | ADR-059/V10 aggregateur bootstrap |
| `adapters/clipool/clipool_worker.py` | 382 | WorkerError + exception classifier (#1016, #1215) |

- **Gate status** : `check_file_length.sh` → exit 0 (pas de violations hors exemptions)

### folder_size (max=12)

- **5 dossiers exemptés** (`tools/folder_exemptions.txt`) :

| Dossier | Fichiers | Raison |
|---------|---------|--------|
| `src/lyra/core` | 15 | config dataclass + logging_setup (#858, #1020) |
| `src/lyra/infrastructure/stores` | 15 | Migration ADR-048 (#935) |
| `src/lyra/core/cli` | 14 | cli_pool_entry.py extraction (#957) |
| `src/lyra/core/messaging` | 13 | WorkerError + tool_recap (#1016, #1031) |
| `src/lyra/nats` | 14 | Pattern intentionnel ADR-049 (#1221) |

- **Gate status** : `check_folder_size.sh` → exit 0

### duplicate_test_basenames

- `check_duplicate_test_basenames.sh` → exit 0 (aucun doublon)

### Synthèse couverture gates

**Ce que les gates capturent** : dérive de taille fichier/dossier, imports inter-couches, doublons basename tests.

**Ce qu'ils NE capturent PAS** : dead code, couplage sémantique inter-modules, duplication logique, abstraction manquante, conformité hexagonale au niveau Port/Adapter (uniquement les dépendances Python détectées).

---

## 4. Dette & audits préexistants

### Résumé debt/INDEX.md (18 slugs)

| Règle/Outil | Slugs | Drain slice |
|-------------|-------|-------------|
| `importlinter` | `importlinter-adr048-transition`, `importlinter-shared-modules-transitive` | `#1163` |
| `file-length / folder-size` | `file-exemptions`, `folder-exemptions` | `#1163` |
| `C901` (complexité) | `adapter-dispatch-complexity`, `complexity-residual`, `migration-sequence-bootstrap`, `wiring-bootstrap-deps` | P2b / async-pipeline |
| `BLE001` (broad catch) | `boundary-broad-catch` | async-pipeline |
| `pyright` | `defensive-narrow-payloads`, `protocol-private-ducktyping` | async-pipeline |
| `PLR0913 / PLR0915` | `wiring-bootstrap-deps`, `migration-sequence-bootstrap` | async-pipeline |
| `F401 / E402` | `module-level-patch-fixtures`, `re-export-init` | async-pipeline |
| `PLR2004 / A002 / D401 / B008 / PLC0415` | `adapter-magic-constants`, `lint-residual`, `d401-property-accessor`, `typer-default-option`, `plc0415-deferred-import` | P2b |

Tous les 18 slugs ont statut **open**.

### Résumé audits #1145

- **1145-adr-audit** (`artifacts/1145-adr-audit.md`) — T1 : audit des 72 ADRs ; classification archive/bucket/live ; identification 5 buckets thématiques (NATS Core → ADR-045, Contracts → ADR-049, Storage → ADR-048, Streaming → ADR-059, Deploy → Quadlet). ADR-036, ADR-066 identifiés comme drifted / à rattacher.

- **Cluster A** (`artifacts/1145-cluster-A-arch-layering-audit.md`) — 14 ADRs vérifiés code-as-truth. ADR-002 (pool atomicity) et ADR-006 (hub run-loop) ont drifted vers l'architecture middleware pipeline. ADR-016 (LlmProvider/AnthropicSdkDriver) = FULL-ARCHIVE confirmé.

- **Cluster B** (`artifacts/1145-cluster-B-nats-contracts-audit.md`) — 17 ADRs NATS/contracts/voice. ADR-035 stale status ("Draft" mais impl complète). ADR-036 diverge du wire format réel (ADR-040 Finding 2 = vérité). Contrats roxabi-nats et roxabi-contracts validés conformes.

- **Cluster C** (`artifacts/1145-cluster-C-hub-adapters-audit.md`) — 21 ADRs hub/adapters/dispatch. ADR-001/003/004/005 = KEEP-LIVE-ACCURATE. ADR-006 = drifted (pipeline middleware). ADR-021 = FULL-ARCHIVE.

- **Cluster D** (`artifacts/1145-cluster-D-deploy-streaming-audit.md`) — 20 ADRs deploy/streaming. ADR-012/016/018 = FULL-ARCHIVE (AnthropicSdkDriver disparu). Pattern `deploy/quadlet/*.container` confirmé conforme.

### Findings #1145 : résolus vs ouverts

- **Résolu depuis** : `lyra.stt` et `lyra.tts` supprimés (commits `90fc15ed`, `9ce5f181`) — finding Cluster B sur modules legacy tts/stt = clos. ADR-035 status toujours "Draft" en fichier = **toujours ouvert**.
- **Non vérifiable rapidement** : statut exact des 72 ADRs post-consolidation (la consolidation ADR elle-même n'a pas été mergée dans cette branche — `docs/architecture/adr/` a encore 50+ fichiers).

---

## 5. Anomalies structurelles évidentes

- `src/lyra/agent_cmd/` et `src/lyra/agents/` — noms similaires, rôles distincts : `agents/` contient les implémentations AgentBase ; `agent_cmd/` contient les commandes CLI `lyra agent`. Absent du tableau CLAUDE.md racine — `src/lyra/agent_cmd/`

- `src/lyra/config/` est un répertoire de fichiers TOML (¬module Python, ¬`__init__.py`), mais `src/lyra/config.py` est le vrai module Python `lyra.config`. Le dossier `config/` est invisible à Python et confusant — `src/lyra/config/` vs `src/lyra/config.py`

- `src/lyra/core/smart_routing_protocol.py` (45 LOC) n'est importé par **aucun** autre module (`grep` = 0 hits) — potentiel dead code ou module orphelin — `src/lyra/core/smart_routing_protocol.py`

- `src/lyra/llm/smart_routing.py` (5 LOC) coexiste avec `src/lyra/core/smart_routing_protocol.py` — naming collision entre deux fichiers de rôles différents — `src/lyra/llm/smart_routing.py` vs `src/lyra/core/smart_routing_protocol.py`

- `src/lyra/infrastructure/audit/` (103 LOC, `jetstream_sink.py`) est un sous-module infrastructure isolé non mentionné dans le CLAUDE.md infrastructure — `src/lyra/infrastructure/audit/jetstream_sink.py`

- `tests/integration/` et `tests/integrations/` coexistent — `integration` contient des tests e2e command sessions/pipeline/reasoning ; `integrations` contient supervisor/systemctl/vault — confusion potentielle sur la convention — `tests/integration/` vs `tests/integrations/`

- `src/lyra/obs/` (199 LOC) n'a pas de CLAUDE.md et n'apparaît pas dans le tableau CLAUDE.md racine malgré être listé comme "shared floating" dans `.importlinter` — `src/lyra/obs/`

- `src/lyra/core/stores/` (7 fichiers = protocoles + `json_agent_store.py`) coexiste avec `src/lyra/infrastructure/stores/` (15 fichiers = implémentations SQLite) — boundary ADR-048 en cours de migration — voir DEBT:importlinter-adr048-transition

---

## 6. Questions ouvertes pour les 3 audits suivants

### A1 — Hexagonal (ports/adapters, leaks)

1. Les 3 ports dans `core/ports/` (llm, tts, stt) sont-ils les seuls ports formellement définis, ou d'autres interfaces implicites existent dans `core/stores/` et `core/commands/` ?
2. Les 11 exemptions TYPE_CHECKING `core → infrastructure` représentent-elles de vraies violations de la dependency rule, ou uniquement des annotations de type statiques (sans import runtime) ?
3. `src/lyra/nats/` est classé en couche `llm | nats` (même niveau) — est-ce cohérent que les clients NATS `nats_llm_client.py` (628 LOC) implémentent la logique de transport ET une partie de la logique applicative LLM ?
4. `adapters/nats/` (adaptateur NATS inbound) vs `src/lyra/nats/` (clients NATS outbound) — où est la frontière Port/Adapter dans le transport NATS bidirectionnel ?

### A2 — Mutualisation (duplications, candidats roxabi-*)

1. `adapters/telegram/telegram_outbound.py` et `adapters/discord/discord_outbound.py` partagent-ils suffisamment de logique rendering (reasoning, tool recap) pour justifier une extraction dans `adapters/shared/` ou un package ?
2. `nats/render_event_codec.py` (374 LOC) encode/décode des RenderEvents Lyra-spécifiques — quelle part est couplée à Lyra vs généralisable dans `roxabi-nats` ?
3. `core/cli/cli_streaming_parser.py` (336 LOC) et `nats/render_event_codec.py` manipulent tous deux la structure streaming — y a-t-il duplication de parsing ?
4. `llm/errors.py` (5 LOC) et `src/lyra/errors.py` (93 LOC) — sont-ils distincts ou redondants ?

### A3 — Simplification (over-engineering, dead code)

1. `src/lyra/core/smart_routing_protocol.py` (0 imports entrants) — dead code confirmé ou usage indirect (duck typing, `isinstance` check dans llm/) ?
2. `bootstrap/factory/wiring_helpers.py` (410 LOC, exempté) — est-il un agrégateur légitime ou un God module mélange de préoccupations ?
3. `src/lyra/infrastructure/infra/jetstream_sink.py` — rôle exact et usage ; pourquoi isolé dans `infrastructure/audit/` plutôt que `infrastructure/stores/` ?
4. Les 6 fichiers CLI racine (`cli_voice_smoke.py`, `cli_ops.py`, `cli.py`, `cli_agent.py`, etc.) — y a-t-il duplication de setup Typer/options entre eux ?

---

## 7. Caveats & non-couvert

- **Import linter sur packages/** : `lint-imports` analyse uniquement `src/lyra` (364 fichiers). Les packages `roxabi-nats` et `roxabi-contracts` ne sont pas couverts par les contracts `.importlinter`.
- **Status ADR post-consolidation** : la consolidation issue #1145 (domain pages) n'est pas encore merged dans cette branche. Les 50+ fichiers ADR plats restants n'ont pas été lus individuellement — statut exact de chaque ADR = non vérifié.
- **Tests coverage maps** : le contenu des 317 fichiers de tests n'a pas été cartographié (hors scope cartographie, pertinent pour A2/A3).
- **`src/lyra/tools/gh_token`** : sous-répertoire `gh_token` non exploré (mentionne un submodule dans CLAUDE.md outils).
- **`packages/roxabi-contracts/src/roxabi_contracts/cli/`**, **`gh/`**, **`jobs/`** : structure listée mais non lue en détail.
- **`cocoindex-code:ccc`** : non utilisé (jugement : index sémantique non nécessaire pour cartographie structurelle).
