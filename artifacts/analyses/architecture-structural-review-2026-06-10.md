# Revue structurelle — architecture, packages, axial drift

> Date : 2026-06-10 · Demandeur : Mickael · Auteur : Claude (Fable 5, synthèse) sur exploration par 5 sub-agents ∥ (axial-adr-review + 4 Explore)
> Périmètre : **code réel** — `src/factory/` (~41k lignes), `packages/` (~17k lignes), `.importlinter`, ADR-073. Complète la revue **stratégique** du 06-09 (`architecture-critical-review-2026-06-09.md` — vision, décisions G/E/C/F, backlog) sans la dupliquer.
> Méthode : claim → evidence (file:line lue) → confiance. Claims HIGH re-vérifiés par le lead (gh + lecture source). `CURRENT.generated.md` jamais cité comme preuve comportementale (grants ≠ callsites).

---

## TL;DR — verdict

**L'axe stage (ADR-073) tient.** Signature de drift primaire absente, adapters minces, nouveaux stages (blobstore, turn-writer) ajoutés sur le bon axe, importlinter réel (11 contrats, dette taggée et **en décroissance**). Packages factory-agnostiques avec inversion de dépendance propre. **Mais** : un revisit-trigger officiel de l'ADR-073 est déclenché (broad-catch ×5 la cible, #1284 fermé), l'invariant SanitizedError single-site a été relâché sans amendement d'ADR, trois poches d'infra vivent dans `core/`, et les deux chemins de wiring (unified vs 5-process) ont **prouvé** leur capacité à diverger (audio absent en unified, pointeur de suivi stale).

| Domaine | Verdict | Résumé |
|---|---|---|
| Axe stage (ADR-073) | 🟢 tient | 0 `Client.*Client`, adapters = compositions ~15 lignes, 1 trigger déclenché |
| Hexagone / core | 🟡 sain | Ports/protocols disciplinés, hub 222 lignes (5 mixins) ; infra-in-core ×3 |
| Packages | 🟢 propres | contracts ← nats ← factory, 0 import `factory.*`, 2 violations mineures |
| Adapters / stages | 🟢 minces | Ingest ADR-083 sans bypass ; SanitizedError 10 sites vs 1 prescrit |
| Bootstrap / infra | 🟡 fragile | 2 chemins de wiring parallèles qui divergent ; stores exemplaires |

---

## 1. Axial drift — état des triggers ADR-073

#1284 (Phase 7) **fermé 2026-05-31** → les cibles "post-epic" sont actives.

| Trigger ADR-073 | Mesuré (2026-06-10) | Déclenché |
|---|---|---|
| `boundary-broad-catch` > 30 post-Phase 7 | **150** `except Exception` / 91 fichiers (vérifié lead) | **OUI — ×5** |
| `wiring-bootstrap-deps` > 30 post-Phase 7 | ~36 / 14 fichiers bootstrap | limite |
| `class.*Client.*Client` (nats/transport/packages) | 0 match | non |
| Coût nouvelle intégration > 200 lignes | blobstore 7 fichiers, turn-writer 4 — conformes | non |
| Revue semestrielle | due 2026-11-01 | non |
| Sibling-fix rate > 3/sem | non mesurable statiquement | non audité |

Lecture du trigger broad-catch : beaucoup de sites sont taggés `DEBT:boundary-broad-catch` (dette suivie, pas accumulation silencieuse), et les sites adapters/HTTP-boundary sont attendus par l'ADR. Mais la cible ≤30 était l'engagement de l'epic — soit on la burn-down (en priorisant core/ et inbound/, là où le risque N×M est réel), soit on **re-baseline l'ADR explicitement**. L'état actuel = trigger officiel ignoré.

Drift latent (1 strike sur 3) : `if d.platform == "cli":` dans `core/auth/authenticator.py:288` — sentinel data-driven acceptable seul ; un 2ᵉ sentinel plateforme dans core/ ⇒ extraire un port `PlatformAuthPolicy`.

---

## 2. Ce qui tient (à ne pas re-litiger)

| Choix | Evidence | Verdict |
|---|---|---|
| Adapters = compositions minces | `_make_emitter` ≈ 15 lignes (telegram.py:284-310, adapter.py:265-291) ; `send_streaming()` concret sur `OutboundAdapterBase`, jamais overridé | ✅ N+M réel |
| Primitives partagées uniques | `shared/_emitter.py` (54 l.), `chunk_text`, `parse_reply_to_id` — 1 définition chacune ; `make_typing_factory` imposé par adapters/CLAUDE.md | ✅ |
| Ingest central ADR-083 | telegram/discord construisent tous deux `InboundPipeline(ingest_stage=AttachmentIngestStage())` — aucun bypass ; no-store path purge les closures (`pipeline.py:92-99`) | ✅ |
| Dispatcher sans branche plateforme | `core/hub/outbound/_dispatch.py` — 100 % via protocol `ChannelAdapter` | ✅ |
| Nouveaux stages sur le bon axe | blobstore (FastAPI + `BlobStorePort`), turn-writer (JetStream, writer unique de turns.db) | ✅ |
| Packages factory-agnostiques | 0 import `factory.*`/`lyra.*` en prod ; roxabi-blobs a une assertion AST qui verrouille la frontière (`test_ingest.py:338-343`) | ✅ |
| Inversion de dépendance 3-layers | contracts (pydantic only) ← nats (nats-py+contracts) ← factory.nats ; 0 cycle ; `WorkerPoolClient` injecté via Protocols structurels | ✅ |
| Subjects SSoT (llm/voice/image) | dataclasses frozen Literal-typed dans roxabi-contracts — typo = erreur de type | ✅ (sauf clipool, cf. F-4) |
| Dette importlinter décroissante | git log .importlinter : exemptions retirées (TurnStore, authenticator) ; reste 7 TYPE_CHECKING taggées ADR-048 | ✅ trend |
| Gate 300-SLOC sans exemption | `tools/file_exemptions.txt` vide ; max ~291 SLOC | ✅ |
| Hub & CliPool décomposés | hub.py 222 l. / 5 mixins ; cli_pool.py 208 l. / 5 mixins | ✅ |
| Discipline PII | 10/10 sites SanitizedError utilisent `type(exc).__name__`, jamais `str(exc)` | ✅ |
| Stores infra | `SqliteStore` base : WAL, checkpoint 1800s, busy_timeout, write-through cache — uniforme, 0 logique métier | ✅ |
| bootstrap/types.py neutre | 134 l., imports 100 % TYPE_CHECKING, dataclasses uniquement | ✅ |

---

## 3. Findings (consolidés, dédupliqués inter-agents)

### Haute

| # | Finding | Evidence | Confiance |
|---|---|---|---|
| F-1 | **Trigger ADR-073 broad-catch déclenché** : 150 sites vs ≤30, #1284 fermé. Décision requise : burn-down (core/+inbound/ d'abord) ou re-baseline ADR | grep lead : 150/91 fichiers ; gh #1284 CLOSED 2026-05-31 | haute |
| F-2 | **Parité unified/standalone cassée sur l'audio** : `factory start` ne démarre pas `JetStreamAudioConsumer` (voice silencieusement absent en unified) ; le commentaire pointe "#1521" or **#1521 est fermé sur un autre sujet** (contrat outbound-audio) — le suivi est un dangling pointer, aucune issue ouverte ne couvre le gap | `bootstrap/factory/unified.py:92-97` (lu lead) ; gh #1521 CLOSED | haute |
| F-3 | **`core/cli/` = infra OS dans le domaine** : subprocess exec, PIDs, env allowlists, `_LYRA_ROOT` à l'import (`cli_pool_spawn.py:59,80-110`) + 2ᵉ site subprocess indépendant dans `core/agent/agent_refiner.py:125-149`. À coupler au verdict runtime pluggable (#1812 OmpWorker / #1813) : extraire un boundary runtime **avant** d'ajouter un 2ᵉ runtime, sinon le pattern infra-in-core se duplique | cli_pool_spawn.py, agent_refiner.py | haute |

### Moyenne

| # | Finding | Evidence | Confiance |
|---|---|---|---|
| F-4 | Subjects `factory.clipool.*` hardcodés en littéraux dans 3 fichiers, 0 SSoT contracts (contraste avec llm/voice/image) — à rattacher à #1793 (taxonomie subjects) | `clipool_worker.py:36-38`, `llm_client.py:34`, `hub_llm_client.py:33,42` | haute |
| F-5 | **SanitizedError : 10 sites vs 1 prescrit par ADR-073** — 6 en transport/ (ok), 1 outbound (documenté CLAUDE.md), 2 `stream_processor.py:203,232` + 1 `cli_streaming_parser.py:251` **non actés**. PII ok partout ; l'invariant a été relâché de facto → amender l'ADR ou consolider | sites listés | haute |
| F-6 | `OutboundDispatcher._queue` **unbounded** (`asyncio.Queue()` sans maxsize) — confirmé src : OOM possible sous panne adapter prolongée, enqueue fire-and-forget sans backpressure. *(Correctif 06-11 : `adapters.md:199` acte le gap explicitement — pas de drift doc-code, contrairement à la lecture initiale)* | `core/hub/outbound/outbound_dispatcher.py:60` (lu lead) | haute |
| F-7 | `ProcessorRegistry` = singleton module-level mutable + enregistrement par side-effect d'import (`importlib.import_module` à `command_router.py:119`) — incohérent avec le DI-first ; recoupe le finding 06-09 (registry absent du chemin streaming) | `processor_registry.py:135,139` | haute |
| F-8 | Implémentations concrètes dans core/ : `core/stores/json_agent_store.py` (file I/O, 236 l. — le pattern documenté dit "impls SQLite en infrastructure") et `core/memory/memory_schema.py:12-96` (DDL SQLite complet, triggers, FTS) | fichiers lus | haute |
| F-9 | **Wiring = 2 chemins parallèles qui divergent** : TypingListener câblé inline en unified vs closures per-bot en standalone ; F-2 = preuve que la divergence arrive réellement. Wiring 100 % impératif (noqa C901/PLR0915 taggés DEBT) | `wiring_helpers.py` vs `standalone_telegram.py:107-112` | haute |
| F-10 | `cli_ops.py:27` importe `_build_tls_context` — fonction privée d'un module public, non couverte par l'exemption ADR-045 (qui n'autorise que les sous-modules `_`-préfixés) | cli_ops.py:27 | haute |

### Basse / info

| # | Finding | Evidence |
|---|---|---|
| F-11 | `send_with_retry()` avale la dernière exception silencieusement (0 log) après 3 retries | `adapters/shared/_shared.py:121` |
| F-12 | `DISCORD_MAX_LENGTH = 2000` dans `shared/` — constante plateforme dans la lib de primitives | `adapters/shared/_shared.py` |
| F-13 | Telegram inline son `InboundContext` ×2 (text/voice) là où Discord a un extracteur — extracteur `build_telegram_inbound_ctx()` manquant | `telegram_inbound.py:105-123,277-295` |
| F-14 | `NatsTransport` (factory.transport) ∥ `NatsDriverBase` (roxabi-nats) : 2 couches call/inbox nats-py — **by design** (CLAUDE.md transport l'acte), surface de maintenance dupliquée à surveiller | nats_request_response.py vs driver_base.py |
| F-15 | ~26 lectures `os.environ` éparpillées dans bootstrap/, 0 manifeste central ; `embedded_nats.py:172` **écrit** dans os.environ au runtime | grep bootstrap/ |
| F-16 | Imports profonds contournant les ré-exports publics : `hub_llm_client.py:27` (`roxabi_contracts._nats_utils`), `worker_pool_client.py:19` (`roxabi_nats.circuit_breaker`) | fichiers cités |
| F-17 | `NatsTgWiringDeps`/`NatsDcWiringDeps` dépréciés non supprimés ; `hub_builder.py` = façade 6 lignes au nom trompeur | nats_wiring.py, hub_builder.py |
| F-18 | Gap d'enforcement : grep pre-commit `str(exc)` bus-bound scopé hors #1279 — la discipline PII (✅ aujourd'hui) n'a aucun garde-fou automatisé | outbound/CLAUDE.md:76-78 |
| F-19 | Pre-commit/pre-push : 7 exemptions TYPE_CHECKING restantes (ADR-048) dont 3 sur le constructeur de Hub (types concrets `PairingManager`, `PrefsStore` en signature) | .importlinter, hub.py:35-37 |

---

## 4. Recommandations priorisées

| # | Action | Findings | Quand |
|---|---|---|---|
| 1 | Trancher le trigger broad-catch : issue burn-down 150→≤30 (core/+inbound/ d'abord) **ou** amendement ADR-073 re-baselinant la cible | F-1 | court terme |
| 2 | Issue parité audio unified-mode (ou décision écrite « unified = dev-only, exclusions listées ») + corriger le pointeur #1521 stale dans unified.py | F-2, F-9 | court terme |
| 3 | Borner `OutboundDispatcher._queue` (maxsize + politique overflow) et réaligner la doc | F-6 | court terme |
| 4 | Lot S « hygiène packages » : subjects clipool → roxabi-contracts (rattacher #1793), fix import privé cli_ops, imports profonds → ré-exports, purge deps dépréciées | F-4, F-10, F-16, F-17 | court terme |
| 5 | Amendement ADR-073 : acter les 4 sites SanitizedError hors transport (ou consolider) + ajouter le grep pre-push `str(exc)` bus-bound (gate bon marché) | F-5, F-18 | ≤2 sem |
| 6 | **Coupler l'extraction `core/cli/` au chantier runtime pluggable (#1812/#1813)** : définir le port runtime dans core/ports, déplacer subprocess/process-mgmt hors du domaine au moment d'introduire OmpWorker — pas avant (éviter le refactor à vide), pas après (éviter 2 runtimes infra-in-core) | F-3 | au verdict #1812 |
| 7 | DI-ifier `ProcessorRegistry` (injection explicite, suppression du side-effect d'import) — à fusionner avec l'issue 06-09 « registry sur chemin streaming » | F-7 | post-slice |
| 8 | Déplacer json_agent_store + memory_schema vers infrastructure/ (ou exemption documentée) ; noter que memory_schema sera de toute façon impacté par l'arbitrage cortex (revue 06-09 §6) | F-8 | opportuniste |

---

## Non audité (consolidé)

- Sibling-fix rate (trigger ADR-073) — nécessite analyse git log temporelle
- `core/` : lifecycle/, messaging/events.py, pool/ mixins restants, auth/guard.py, integrations/ — non lus
- Chemins audio adapters (`*_audio.py`) — non vérifiés pour ré-implémentation de stage
- Internals roxabi-nats : circuit_breaker, readiness — non lus
- roxabi-contracts image/ et turns/ — qualité SSoT non vérifiée ; `factory.turns.write` SSoT non vérifié
- `infrastructure/stores/` au-delà de agent/auth/base — non inspectés
- `bootstrap/infra/` (hors embedded_nats), ingest_wiring, kv_watch_channels, watchdog — non lus
- Edge-cases `send_chunked_message` (streams zéro-chunk, interleaving attachments) — non testés contre le code
- Couverture test de la divergence unified/standalone — non vérifiée
