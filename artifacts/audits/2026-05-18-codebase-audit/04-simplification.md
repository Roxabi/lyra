# 04 — Audit simplification (A3)

## Verdict global

Le codebase Lyra contient un volume de gras modéré, majoritairement localisé dans deux zones :
dead code vestigial post-feature (#666 smart routing) et modules isolés sans consommateur runtime.
Les fichiers obèses exemptés ont des raisons documentées légitimes (temporalité Slice 5 / #1102) —
ce ne sont pas des God modules mais des agrégats transitoires conscients.
Les deux leviers majeurs : (1) suppression du cluster smart_routing mort (~45+100 LOC + tests associés),
(2) raccordement ou suppression de `lyra.obs` qui est entièrement sans consommateur runtime.

## Top 10 findings

| # | Titre | Localisation | Type | Effort | Impact | Recommandation |
|---|-------|-------------|------|--------|--------|----------------|
| 1 | SmartRoutingProtocol sans aucun caller | `core/smart_routing_protocol.py:1-45` | dead-code | S | fort | Supprimer le fichier entier |
| 2 | `lyra.obs` sans consommateur runtime | `src/lyra/obs/` (199 LOC) | dead-code | S | fort | Supprimer ou déplacer dans packages/ si future Langfuse |
| 3 | `llm/smart_routing.py` shim vide | `llm/smart_routing.py:1-5` | dead-code | S | moyen | Supprimer ; `SmartRoutingConfig` vit déjà dans `core/agent/agent_config.py` |
| 4 | `llm/errors.py` shim 6 lignes | `llm/errors.py` | dead-code | S | moyen | Supprimer ; migrer les 0 callers réels vers `lyra.errors` |
| 5 | `PlatformCallbacks.edit_trace` vestigial | `adapters/shared/_shared_streaming_emitter.py:78-80` | dead-code | S | moyen | Supprimer le champ (#1214 documente qu'il est vestigial) |
| 6 | `wiring_helpers.py` 1 caller unique | `bootstrap/factory/wiring_helpers.py:1-410` | abstraction-1-caller | L | moyen | Pas de split — inliner dans `unified.py` ou garder comme façade documentée |
| 7 | `agent_cmd/` absent du tableau CLAUDE.md | `src/lyra/agent_cmd/` | structure-doublon | S | faible | Ajouter entrée CLAUDE.md racine ; ne pas fusionner avec `agents/` |
| 8 | `render_event_codec.py` exemption à expirer | `nats/render_event_codec.py:374 LOC` | exemption-obsolète | M | moyen | S4 (#1192) déjà planifié — vérifier issue ouverte ; retirer exemption quand <300 |
| 9 | `config/` TOML vs `config.py` Python | `src/lyra/config/` vs `src/lyra/config.py` | structure-doublon | S | faible | Renommer `config/` en `data/` ou `toml/` pour éliminer la collision nominale |
| 10 | `tests/integration/` vs `tests/integrations/` | `tests/integration/` + `tests/integrations/` | structure-doublon | S | faible | Harmoniser convention (choisir `integration` ou `integrations`) |

## Détail des Top 10

### 1. SmartRoutingProtocol sans aucun caller

- **Où**: `src/lyra/core/smart_routing_protocol.py:1-45`
- **Constat**: `grep -rn "SmartRoutingProtocol\|RoutingDecision" src/ tests/ packages/` → 0 hits hors le fichier lui-même. Module documenté comme pont architectural (`core/` expose le Protocol pour que `llm/` ne soit pas importé depuis `core/`), mais `llm/smart_routing.py` est vide depuis #666 et plus aucun code n'instancie `SmartRoutingDecorator`. `Complexity` enum importée depuis `core/agent/agent_config.py` uniquement dans ce fichier.
- **Pourquoi simplifiable**: Le decorator a été retiré en #666. Le Protocol structurel n'a plus d'implémenteur ni de consommateur. Il représente une interface sans implémentation et sans usage.
- **Risque de suppression**: Zéro runtime. Un test `tests/llm/test_smart_routing_toml.py` importe `Complexity, SmartRoutingConfig` depuis `core/agent/agent_config.py` directement — pas depuis ce fichier. Aucune dépendance PyPI ou runtime vers `SmartRoutingProtocol`.
- **Recommandation**: Supprimer `smart_routing_protocol.py`. Supprimer `llm/smart_routing.py` simultanément (see #3). Le `Complexity` enum reste dans `agent_config.py` car `SmartRoutingConfig` l'utilise.

### 2. `lyra.obs` sans consommateur runtime

- **Où**: `src/lyra/obs/` — `base.py` (119 LOC), `noop.py` (65 LOC), `__init__.py` (17 LOC) = 201 LOC total
- **Constat**: `grep -rn "from lyra.obs\|lyra\.obs\." src/` → 0 hits hors `obs/` lui-même. `grep` dans `tests/` → 1 fichier : `tests/obs/test_obs_base.py`. Module listé dans `.importlinter` comme "shared floating" avec contrat `shared-modules-independence` et `shared-modules-upper-boundary` actifs, mais aucun module de production ne l'importe.
- **Pourquoi simplifiable**: Infrastructure d'observabilité (OTel/Langfuse/NoOp) préparée mais jamais câblée dans le hub, les agents, ou les adapters.
- **Risque de suppression**: Suppression = retrait d'une feature future, non d'une feature en prod. Tests `test_obs_base.py` tombent. Les contrats importlinter qui le listent doivent être mis à jour.
- **Recommandation**: Si Langfuse est toujours dans le roadmap, déplacer dans `packages/roxabi-obs`. Sinon supprimer. Trancher avant Slice 5.

### 3. `llm/smart_routing.py` shim vide

- **Où**: `src/lyra/llm/smart_routing.py:1-5`
- **Constat**: 5 lignes de docstring uniquement. "Decorator removed, #666." `SmartRoutingConfig` vit dans `core/agent/agent_config.py:131`. Le fichier lui-même n'exporte rien. `grep -rn "from lyra.llm.smart_routing\|from lyra.llm import smart_routing"` → 0 hits.
- **Pourquoi simplifiable**: Fichier vide de code, 0 callers.
- **Risque de suppression**: Aucun — `SmartRoutingConfig` n'est pas dans ce fichier.
- **Recommandation**: Supprimer avec #1 en même PR.

### 4. `llm/errors.py` shim 6 lignes

- **Où**: `src/lyra/llm/errors.py`
- **Constat**: Confirmé par A2 — 6 lignes, aucune valeur ajoutée. `grep -rn "from lyra.llm.errors"` → non vérifié exhaustivement dans cette session mais A2 rapporte 0 callers réels.
- **Pourquoi simplifiable**: Shim redirigeant vers `lyra.errors` sans usage.
- **Risque de suppression**: Si 0 callers confirmés par A2, aucun risque runtime.
- **Recommandation**: Supprimer. Vérifier `grep -rn "from lyra.llm.errors"` avant merge.

### 5. `PlatformCallbacks.edit_trace` vestigial

- **Où**: `src/lyra/adapters/shared/_shared_streaming_emitter.py:78-80`
- **Constat**: Documenté vestigial dans #1214 — champ de callback présent dans le contrat `PlatformCallbacks` mais sans implémentation active dans les adapters Telegram/Discord. Reste dans la dataclass et pollue le contrat.
- **Pourquoi simplifiable**: Dead field dans un contrat inter-couches — tout nouveau adapter doit l'implémenter pour rien.
- **Risque de suppression**: Casser les constructeurs `PlatformCallbacks(...)` partout où il est fourni. Chercher `edit_trace` dans adapters avant suppression.
- **Recommandation**: Retirer le champ + les sites d'appel. Impact estimé ≤3 fichiers.

### 6. `wiring_helpers.py` — agrégateur à 1 caller

- **Où**: `src/lyra/bootstrap/factory/wiring_helpers.py:1-410`
- **Constat**: `grep -rn "from lyra.bootstrap.factory.wiring_helpers"` → 1 caller unique : `bootstrap/factory/unified.py:14` qui importe 9 fonctions. `hub_standalone.py` utilise `hub_builder.py` directement, pas `wiring_helpers`. Le fichier contient 3 dataclasses bundle + 10 fonctions `_init_*` focalisées.
- **Pourquoi simplifiable**: Abstraction à 1 caller. Mais les fonctions sont cohérentes (phase helpers pour le bootstrap unifié) et la séparation est intentionnelle (ADR-059/V10). Split = inliner dans `unified.py` qui deviendrait 500+ LOC.
- **Risque de suppression**: Moyen — `unified.py` grossit, mais gain de lisibilité faible.
- **Recommandation**: Garder en l'état. L'exemption est légitime. Pas un God module — 10 fonctions focalisées. Marquer "pas un candidat au split" dans CLAUDE.md bootstrap.

### 7. `agent_cmd/` absent du tableau CLAUDE.md

- **Où**: `src/lyra/agent_cmd/` — 520 LOC, 4 fichiers
- **Constat**: Listé dans `shared-modules-independence` contract importlinter mais absent du tableau CLAUDE.md racine. `cli_agent.py:88` l'importe via `importlib.import_module("lyra.agent_cmd.agents")`. Rôle distinct de `agents/` (CLI vs implémentation agent).
- **Pourquoi simplifiable**: Confusion de nommage uniquement — la séparation est justifiée (CLI verbs != agent runtime).
- **Risque de suppression**: N/A — finding structural, pas suppression.
- **Recommandation**: Ajouter entrée dans tableau CLAUDE.md racine avec note "CLI subcommands pour `lyra agent`".

### 8. Exemption `render_event_codec.py` à expirer

- **Où**: `tools/file_exemptions.txt:16`, `src/lyra/nats/render_event_codec.py` (374 LOC)
- **Constat**: Exemption indique "S4 (drop below 300 + remove this exemption) DEFERRED — separate PR required". Le fichier est à 374 LOC depuis #1205. S4 n'a pas encore été mergé dans cette branche.
- **Pourquoi simplifiable**: L'exemption est techniquement obsolescente — la raison originale (v1+v2 side-by-side) est résolue, il reste 74 LOC à extraire pour passer sous 300.
- **Risque de suppression**: Aucun si S4 est fait correctement — découpage codec.
- **Recommandation**: Ouvrir ticket S4 si pas existant ; retirer l'exemption dès que <300.

### 9. `config/` TOML vs `config.py` Python

- **Où**: `src/lyra/config/` (répertoire TOML, ¬module Python) vs `src/lyra/config.py` (module Python)
- **Constat**: `src/lyra/config/` contient `messages.toml` et `patterns.toml`. `src/lyra/config.py` est le vrai module `lyra.config`. Le dossier est invisible à Python mais visible dans l'arborescence — source de confusion à la lecture du tree.
- **Pourquoi simplifiable**: Collision nominale dans l'arborescence.
- **Risque de suppression**: Renommer le dossier = mettre à jour les chemins de chargement TOML dans le code (probablement 1-2 sites).
- **Recommandation**: Renommer `src/lyra/config/` en `src/lyra/data/` ou `src/lyra/config_data/`.

### 10. `tests/integration/` vs `tests/integrations/`

- **Où**: `tests/integration/` et `tests/integrations/`
- **Constat**: Deux dossiers de tests dont les noms diffèrent uniquement par le pluriel. `integration/` contient tests e2e command sessions ; `integrations/` contient supervisor/systemctl/vault. La distinction sémantique est réelle mais la convention est incohérente.
- **Pourquoi simplifiable**: Confusion pour les nouveaux contributeurs.
- **Risque de suppression**: Aucun si les paths pytest sont mis à jour.
- **Recommandation**: Harmoniser en `tests/integration/` (singulier) avec sous-dossiers `e2e/` et `external/`.

## Findings additionnels

- `src/lyra/core/processors/stream_processor.py` (630 LOC, exempté) — 1 seule classe `StreamProcessor` + 3 helpers privés. Préoccupation unique (LlmEvent→RenderEvent pipeline). Pas un God module. Exemption justifiée en attente Slice 5.
- `_shared_streaming_emitter.py` (465 LOC) — 2 classes cohérentes (`PlatformCallbacks` + `StreamingSession`). Pas un split prioritaire, mais `edit_trace` vestigial (finding #5) devrait être retiré en même temps que #1214 cleanup.
- `nats/nats_llm_client.py` (628 LOC) — 1 classe + 1 helper. Sections clairement découpées (heartbeat, internal helpers, complete, stream). Refactor en shared error-factory documenté dans l'exemption (#1119) — suivi existant.
- `bootstrap/factory/` contient 9 fichiers dont `hub_builder.py` (180 LOC) et `wiring_helpers.py` (410 LOC) qui se chevauchent partiellement — `hub_builder` expose `build_hub` utilisé par `hub_standalone` ; `wiring_helpers._build_hub` est un wrapper. Double-decker léger à surveiller (à transmettre A1 pour hexagonal boundary check).
- `bus.py` Protocol + `inbound_bus.py` LocalBus : 2 implémentations réelles (`LocalBus`, `NatsBus`) — pas une abstraction à 1 caller, pertinent A1 uniquement.
- `SmartRoutingConfig` dans `agent_config.py` : reste nécessaire pour validation TOML (rejet `enabled=true`) — ne pas supprimer même après suppression des fichiers dead.
- Dossier `src/lyra/core/` exempté (15 fichiers) pour raison "#858 config dataclass extraction pending" — si extraction réalisée depuis, l'exemption pourrait tomber. Non vérifié dans cette session.

## Quick wins (≤1h chacun)

- **Supprimer `smart_routing_protocol.py`** : 0 callers, 0 tests directs, 0 risque.
- **Supprimer `llm/smart_routing.py`** : 5 lignes de docstring, 0 callers. Faire en même PR que le point ci-dessus.
- **Ajouter `agent_cmd/` dans le tableau CLAUDE.md racine** : édition documentation pure, aucun risque.
- **Retirer `PlatformCallbacks.edit_trace`** : champ vestigial documenté #1214, impact ≤3 fichiers adapters.
- **Renommer `src/lyra/config/` en `src/lyra/data/`** : 1-2 sites de chargement TOML à mettre à jour.
- **Harmoniser `tests/integrations/` → `tests/integration/`** : mise à jour `pyproject.toml` testpaths + 1 mv.

## Fausses pistes écartées

- **`JetStreamAuditSink` (103 LOC)** — initialement suspect (isolé dans `infrastructure/audit/`). Câblé dans `hub_standalone.py:166` et `wiring_helpers.py:309`. Tests complets (`test_jetstream_audit_provision.py`, `test_cli_spawn_audit.py`). Actif en production. Pas un candidat à la suppression.
- **`ProcessorRegistry` + `BaseProcessor`** — à 1 classe de base mais 4 implémenteurs (`ScrapingProcessor`, `SearchProcessor`, `VaultAddProcessor`, + 1 en test), multiple callers (pool, command_router, agents, tests). Pattern registre légitime, non simplifiable.
- **6 fichiers CLI racine `cli_*.py`** — structurellement cohérents. Chacun déclare 1 `Typer` app assemblée dans `cli.py` via `add_typer`. Pas de duplication setup — chaque app est indépendante. Le pattern est intentionnel pour la modularité des commandes. Collapser ajouterait de la complexité sans gain.
- **`Bus[T]` Protocol + `LocalBus` + `NatsBus`** — 2 implémentations runtime réelles. Le Protocol est justifié pour l'inversion de dépendance hub→transport. Pas une abstraction à 1 caller.
- **`wiring_helpers.py` God module** — 10 fonctions focalisées, 1 seul caller (unified.py). Pas un mélange de préoccupations. L'exemption reflète une décision ADR-059/V10 consciente.

## Hors scope / non vérifié

- `llm/errors.py` : callers non vérifiés exhaustivement par cette session (fourni par A2 — à confirmer par `grep -rn "from lyra.llm.errors"` avant suppression).
- `src/lyra/core/` exemption `#858 config dataclass extraction` : si extraction déjà réalisée, l'exemption est peut-être obsolète — non vérifié.
- `bootstrap/factory/hub_builder.py` vs `wiring_helpers._build_hub` : chevauchement partiel détecté mais non audité complètement.
- Tests bloat (fixtures dupliquées) : non audité — hors top findings.
- `packages/roxabi-nats/` et `packages/roxabi-contracts/` : non couverts par cet audit (hors importlinter scope).
- `src/lyra/tools/gh_token/` : non exploré.
