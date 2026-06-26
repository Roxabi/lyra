# 02 — Audit hexagonal (A1)

## Verdict global

Conformité **partielle, solide sur la structure, fragile sur deux points précis**. Les 7
contracts importlinter passent à 0 violation. Les 11 exemptions `core → infrastructure`
sont toutes de vrais `if TYPE_CHECKING:` — aucune fuite runtime sur ce front. Deux leviers
principaux : (1) `core/stores/pairing_protocol.py` contient un import runtime vers
`infrastructure` déguisé en facade — c'est la seule vraie violation hexagonale hors
dette documentée ; (2) les ports implicites (`ChannelAdapter`, `PipelineMiddleware`,
`SmartRoutingProtocol`, `AgentStoreProtocol×2`) sont dispersés dans `core/` hors
`core/ports/`, créant une surface de découverte difficile. Les couches "shared floating"
respectent leur boundary contract. NATS est correctement positionné en couche `llm|nats`
mais `nats_llm_client.py` (628 LOC) mélange transport et logique applicative.

---

## Top 10 findings

| # | Titre | Localisation | Type | Effort | Impact | Recommandation |
|---|-------|-------------|------|--------|--------|----------------|
| 1 | `pairing_protocol` import runtime infra | `core/stores/pairing_protocol.py:51` | exemption suspecte | S | fort | Extraire le `get_pairing_manager()` vers `bootstrap/` ; retirer le `from lyra.infrastructure` runtime de `core/` |
| 2 | `ChannelAdapter` port hors `core/ports/` | `core/hub/hub_protocol.py:23` | port implicite | M | fort | Déplacer `ChannelAdapter` dans `core/ports/adapters.py` ; aligner avec `LlmProvider`/`TtsProtocol` |
| 3 | `AgentStoreProtocol` dupliqué | `core/stores/agent_store_protocol.py:22` + `core/agent/agent_seeder.py:21` | port implicite | S | moyen | Supprimer la copie locale dans `agent_seeder.py` ; importer de `core/stores/` |
| 4 | `SmartRoutingProtocol` orphelin | `core/smart_routing_protocol.py:33` | port implicite | S | moyen | 0 imports entrants — consolider dans `core/ports/` ou supprimer (→ A3) |
| 5 | `PipelineMiddleware` port hors `core/ports/` | `core/hub/middleware/middleware.py:77` | port implicite | S | moyen | Déplacer dans `core/ports/` ou documenter comme port interne au hub (scope justifié) |
| 6 | `nats_llm_client.py` mixte transport+applicatif | `nats/nats_llm_client.py:155-415` | adapter camouflé | L | moyen | Extraire `_build_request()` + text-folding rule vers un module applicatif ; conserver transport pur dans client |
| 7 | `processor_registry` → `integrations` port non formalisé | `core/processors/processor_registry.py:36` | port implicite | M | moyen | Créer `SessionToolsProtocol` dans `core/ports/` (plan ADR-061 non exécuté) |
| 8 | `monitoring` → `core` import (shared upper boundary) | `monitoring/__main__.py:15` | autre | S | faible | `setup_logging` devrait être dans un module shared (`obs/` ou `errors/`) plutôt que `core/` |
| 9 | `agent_cmd` imports `core` directement | `agent_cmd/agents/init.py:13`, `edit_cmd.py:222,248` | autre | S | faible | Acceptable si `agent_cmd` est traité comme couche application ; documenter dans CLAUDE.md |
| 10 | `OutboundListener` Protocol dans `adapters/shared/` | `adapters/shared/outbound_listener.py:17` | port implicite | S | faible | Port adaptateur-interne légitime ; documenter qu'il ne monte pas vers `core/ports/` (scope intentionnel) |

---

## Détail des Top 10

### 1. `pairing_protocol` import runtime infra

- **Où**: `src/lyra/core/stores/pairing_protocol.py:51`
- **Constat**: `get_pairing_manager()` exécute `from lyra.infrastructure.stores.pairing import get_pairing_manager as _get` à chaque appel runtime (ligne 51, marqué `# noqa: PLC0415 — DEBT:plc0415-deferred-import`). C'est un import runtime, pas `TYPE_CHECKING`.
- **Pourquoi c'est un problème hexagonal**: `lyra.core` contient un import runtime vers `lyra.infrastructure`. Le contract importlinter l'exempte (`.importlinter:32`) mais le slug `DEBT:importlinter-adr048-transition` le reconnaît comme violation transitoire. Contrairement aux 11 autres exemptions qui sont toutes `TYPE_CHECKING`, celle-ci est un vrai couplage runtime.
- **Recommandation**: Déplacer `get_pairing_manager()` vers `bootstrap/factory/` (Composition Root) et injecter via DI. Résout l'exemption `.importlinter:32`.

### 2. `ChannelAdapter` port hors `core/ports/`

- **Où**: `src/lyra/core/hub/hub_protocol.py:23`
- **Constat**: `ChannelAdapter` est le port principal que tous les adapters de canal (Telegram, Discord, clipool, NATS proxy) implémentent. Il est défini dans `core/hub/hub_protocol.py` avec `RoutingKey` et `Binding` — pas dans `core/ports/`. Consommé par `adapters/nats/nats_outbound_listener.py:30`, `adapters/telegram/telegram_inbound.py:82`, `adapters/discord/discord_inbound.py:194`.
- **Pourquoi c'est un problème hexagonal**: Le port primaire de la couche adapters vit dans un sous-module hub plutôt que dans le répertoire officiel des ports. Crée une asymétrie avec `LlmProvider`, `TtsProtocol`, `STTProtocol`.
- **Recommandation**: Créer `core/ports/adapters.py` avec `ChannelAdapter` + `RoutingKey`. `hub_protocol.py` devient un re-export. Migration non-breaking (backward-compat via `__init__`).

### 3. `AgentStoreProtocol` dupliqué

- **Où**: `src/lyra/core/stores/agent_store_protocol.py:22` et `src/lyra/core/agent/agent_seeder.py:21`
- **Constat**: Deux classes `AgentStoreProtocol(Protocol)` avec la même structure (`get`, `upsert`) dans deux fichiers différents. `agent_seeder.py` docstring dit "The narrow `AgentStoreProtocol` in `agent_seeder.py` covers only `get` and `upsert`" — la copie locale n'est pas identique mais fonctionnellement redondante.
- **Pourquoi c'est un problème hexagonal**: Un port avec deux définitions dans le même domaine crée ambiguïté sur la SSoT ; violation du principe port-unique.
- **Recommandation**: Supprimer la définition locale dans `agent_seeder.py` ; importer `AgentStoreProtocol` de `lyra.core.stores`. (→ aussi à transmettre à A3 : duplication).

### 4. `SmartRoutingProtocol` orphelin

- **Où**: `src/lyra/core/smart_routing_protocol.py:33`
- **Constat**: Classe `SmartRoutingProtocol(Protocol)` avec `@runtime_checkable`. Grep sur tout `src/` : **0 imports entrants** — seule la définition existe. Le fichier est dans `core/` à plat, pas dans `core/ports/`.
- **Pourquoi c'est un problème hexagonal**: Port sans consommateur = port mort ou port non relié. Soit il est orphelin (dead code → A3), soit il devrait être dans `core/ports/` et référencé.
- **Recommandation**: Confirmer si `llm/smart_routing.py` implémente structurellement ce protocole (duck typing). Si oui, déplacer vers `core/ports/` et ajouter au moins un isinstance check. Sinon supprimer.

### 5. `PipelineMiddleware` port hors `core/ports/`

- **Où**: `src/lyra/core/hub/middleware/middleware.py:77`
- **Constat**: `class PipelineMiddleware(Protocol)` définit l'interface de chaque middleware du pipeline hub. Consommé uniquement dans `core/hub/middleware/` et `core/hub/pipeline/`.
- **Pourquoi c'est un problème hexagonal**: Techniquement un port interne au hub (pas exposé aux adapters). Hors `core/ports/` mais le scope est justifié (port intra-hub, pas cross-layer).
- **Recommandation**: Documenter explicitement dans `middleware.py` que c'est un port interne au hub (scope intentionnel). ¬déplacer vers `core/ports/` car il ne traverse pas de boundary hexagonale.

### 6. `nats_llm_client.py` mixte transport+applicatif

- **Où**: `src/lyra/nats/nats_llm_client.py:155-415` (`_build_request`, `_complete_request`, `_stream_gen`)
- **Constat**: `_build_request()` (lignes 155-192) implémente une "text-folding rule" spec §3 — logique applicative sur la structure des messages. `_complete_request()` et `_stream_gen()` mélangent gestion NATS transport (timeouts, retry, circuit breaker) et construction de `LlmEvent` domain objects. Le fichier fait 628 LOC, exempté file-length.
- **Pourquoi c'est un problème hexagonal**: La couche `nats` devrait être transport pur ; la logique de construction des messages wire et le mapping vers domain events sont de la logique applicative qui appartient à la couche application ou au port.
- **Recommandation**: Extraire `_build_request` + text-folding vers un `llm_request_builder.py` dans `core/` ou `llm/`. Garder transport pur dans `NatsLlmClient`. Effort L car l'exemption file-length masque la dette de découpe.

### 7. `processor_registry` → `integrations` port non formalisé

- **Où**: `src/lyra/core/processors/processor_registry.py:36`
- **Constat**: `BaseProcessor.__init__` reçoit `tools: "SessionTools"` (import TYPE_CHECKING de `lyra.integrations.base`). `SessionTools` est un dataclass dans `integrations` avec des Protocols internes (`ScrapeProvider`, `VaultProvider`, `AudioConverter`, `ServiceManager`). Pas de `SessionToolsProtocol` dans `core/ports/` — ADR-061 prévoyait de le créer mais ne l'a pas fait.
- **Pourquoi c'est un problème hexagonal**: `core` dépend d'une interface définie dans `integrations` (floating module) plutôt que dans `core/ports/`. La dependency rule demande que core définisse ses propres ports.
- **Recommandation**: Créer `core/ports/integrations.py` avec `SessionToolsProtocol` (plan ADR-061). Supprimer l'exemption `.importlinter` correspondante une fois la migration faite.

### 8. `monitoring` → `core` import

- **Où**: `src/lyra/monitoring/__main__.py:15`
- **Constat**: `from lyra.core.logging_setup import setup_logging`. `monitoring` est un module "shared floating" dont le contract `shared-modules-upper-boundary` interdit les imports vers `bootstrap/adapters/infrastructure` — mais `core` est autorisé. Import fonctionnellement légitime.
- **Pourquoi c'est un problème hexagonal**: Faible — `setup_logging` dans `core/` est une dépendance de setup, pas domaine. Pourrait vivre dans `obs/` pour cohérence avec le rôle shared floating.
- **Recommandation**: Déplacer `core/logging_setup.py` vers `obs/` à long terme. Non urgent, impact faible.

### 9. `agent_cmd` imports `core` directement

- **Où**: `src/lyra/agent_cmd/agents/init.py:13`, `edit_cmd.py:222,248`
- **Constat**: `agent_cmd` importe `lyra.core.agent.agent_config._VALID_BACKENDS` et `lyra.core.agent.agent_refiner.RefinementPatch`. Ces imports sont dans le contract `shared-modules-independence` comme floating module.
- **Pourquoi c'est un problème hexagonal**: `agent_cmd` en tant que CLI applicatif peut légitimement dépendre de `core`. Acceptable architecturalement.
- **Recommandation**: Documenter dans `agent_cmd/CLAUDE.md` que `agent_cmd → core` est intentionnel (couche CLI applicative au-dessus du domaine).

### 10. `OutboundListener` Protocol dans `adapters/shared/`

- **Où**: `src/lyra/adapters/shared/outbound_listener.py:17`
- **Constat**: `OutboundListener(Protocol)` définit l'interface que `NatsOutboundListener` satisfait structurellement. Utilisé par `TelegramAdapter`/`DiscordAdapter` pour typer leur listener interne.
- **Pourquoi c'est un problème hexagonal**: Port adapters-interne légitime. Ne traverse pas de boundary core→adapters. Placement correct dans `adapters/shared/`.
- **Recommandation**: RAS — scope intentionnel. Documenter dans le fichier que c'est un port interne adapters (déjà implicite dans le docstring).

---

## Findings additionnels

- `core/trace.py:28` — `TraceLogRecord(Protocol)` : port implicite pour le logging structuré, hors `core/ports/`, 0 vérification si consommé cross-layer.
- `core/messaging/bus.py:26` — `Bus(Protocol[T])` générique : port de messaging bien placé dans `core/messaging/`, mais pas dans `core/ports/` — cohérence discutable.
- `core/pool/pool_context.py:13` — `PoolContext(Protocol)` : port interne pool, hors `core/ports/`, scope pool-interne justifié (même cas que `PipelineMiddleware`).
- `agents/simple_agent.py:42` → `infrastructure.stores.agent_store` : `TYPE_CHECKING` confirmé. Drain prévu ADR-059 V9.
- `nats/render_event_codec.py` — importe `lyra.core.messaging.render_events` et `roxabi_nats` : codec Lyra-spécifique, non extractable dans `roxabi-nats` sans couplage domaine — à transmettre A2.
- `core/processors/_scraping.py` — DNS lookup + socket dans `core/` : logique I/O dans le domaine. Contenu justifié (SSRF guard), mais idéalement derrière un port `UrlResolver`.
- `infrastructure/audit/jetstream_sink.py` — absent du CLAUDE.md infrastructure ; son `AuditSink` port mentionné dans ADR-059/ADR-057 n'est pas dans `core/ports/`.

---

## Faux positifs écartés

- **11 exemptions TYPE_CHECKING `core → infrastructure`** : toutes vérifiées fichier par fichier — strictement sous `if TYPE_CHECKING:`, zéro import runtime. Non-violations.
- **`processor_registry → integrations` (transitive)** : exemption `.importlinter` correcte ; `integrations/base.py` n'importe que stdlib — pas de couplage upward réel.
- **`agent_cmd → core`** : peut ressembler à une violation shared-floating, mais `agent_cmd` est une CLI applicative dont dépendre de `core` est architecturalement correct.
- **`OutboundListener` dans `adapters/shared/`** : port adapters-interne, ne monte pas vers `core` — pas une violation hexagonale.
- **`monitoring/__main__.py → core.logging_setup`** : autorisé par le contract `shared-modules-upper-boundary` (core n'est pas dans la liste forbid). Faible.

---

## Hors scope / non vérifié

- `bootstrap/wiring/bootstrap_wiring.py`, `bootstrap/lifecycle/`, `bootstrap/standalone/` : non lus en détail pour vérifier absence de logique métier (bootstrap/factory couvert partiellement).
- `nats_llm_client.py` lignes 416-628 (`_stream_gen` complet) : lu en début, structure vérifiée, logique détaillée non exhaustivement auditée.
- `infrastructure/audit/jetstream_sink.py` : présence confirmée, contenu non lu.
- `core/cli/` (14 fichiers) : non audités pour domain leaks — potentiel de logique métier dans `cli_pool_session.py` (session management).
- `adapters/telegram/`, `adapters/discord/` rendering logic : potentiel domain leak (→ A2 selon cartographie question A2-1).
- `packages/roxabi-nats/`, `packages/roxabi-contracts/` : hors scope importlinter, non audités pour conformité hexagonale interne.
- `uv run lint-imports` : non re-exécuté dans cette session (cartographie confirme 0 violations en baseline — considéré valide).

---

## À transmettre A2/A3

- **A2**: `nats/render_event_codec.py` — codec Lyra-spécifique qui importe `lyra.core.messaging.render_events` ; non extractable dans `roxabi-nats` sans couplage domaine. Candidat à analyse duplication vs `core/cli/cli_streaming_parser.py`.
- **A3**: `core/smart_routing_protocol.py` — 0 imports entrants ; dead code probable ou port orphelin. `core/agent/agent_seeder.py:AgentStoreProtocol` — doublon de `core/stores/agent_store_protocol.py`, candidat suppression.
