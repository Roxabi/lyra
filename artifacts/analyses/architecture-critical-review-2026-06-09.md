# Regard critique — architecture Roxabi Factory & décisions à venir

> Date : 2026-06-09 · Demandeur : Mickael · Auteur : Claude (Fable 5, synthèse) sur exploration par 6 sub-agents (sonnet/haiku)
> Périmètre : vision (`~/projects/docs/vision-roxabi-factory.md`), job model (`docs/architecture/job-model.md`), architecture repo, décisions G/E/C/F, epic #1792, spike #1807, **couche mémoire (roxabi-cortex inclus)**.
> Méthode : claim → evidence (file:line) → confiance. Drift docs↔code vérifié sur source réelle, pas grep-assume.

---

## TL;DR — verdict

L'hexagone est réel et bien gardé (importlinter exécuté, axial discipline appliquée). Le job model est le design le mieux raisonné du repo. **Mais** : le backlog implémente le framework générique avant la slice (le piège que la vision dénonce elle-même), la Décision G conflate deux décisions indépendantes (coût-modèle vs harness) sur un framing techniquement faux (`claude -p`), et il y a **trois** designs mémoire concurrents dont aucun n'est arbitré.

| # | Finding | Sévérité |
|---|---|---|
| 1 | **Séquencement inversé** : 9 issues ouvertes = control plane Shape D, **0 issue = slice verticale** | haute |
| 2 | **Décision G mal posée** : conflation coût↔harness ; clipool ≠ `claude -p` ; un stopgap existe ; la slice n'est pas réellement bloquée | haute |
| 3 | **Mémoire ×3** : L0/L3 partiel (code) + `docs/memory-system/` (spec pgvector) + roxabi-cortex (spec DuckDB) — non arbitré, le harness spec pré-sélectionne cortex | haute |
| 4 | **Asymétries prod** : streaming sans circuit-breaker/retry, dual-write turns, ProcessorRegistry absent du chemin streaming | moyenne-haute |
| 5 | Drift vision↔code : « dispatch queue JetStream » ∄, « sessions KV » ∄, `claude -p` faux | moyenne |
| 6 | Tool-system : design verrouillé (D1-D16) mais **parké sans issue d'amorçage** + contradiction D11 interne non résolue | moyenne |
| 7 | `scope_id` vs `user_id` en group chat : à qui appartient la mémoire écrite ? Non résolu — devient bloquant avant cortex Mode 2 | moyenne (latente) |

---

## 1. Ce qui tient (à ne pas re-litiger)

| Choix | Verdict | Pourquoi |
|---|---|---|
| Hexagone + importlinter (11 contrats) | ✅ solide | Enforcement **statique réel**, pas du prose — `.importlinter:11-138`, dette taggée avec expiry |
| `job_id = run`, hops internes opaques | ✅ juste | Dissout l'overload d'identité ; alternatives réellement pesées et rejetées avec raisons (analysis §15.1) |
| `JobResult` = notification ≠ data | ✅ juste | Durabilité au bon étage (turns.db/blobstore) ; refus de double-persister = discipline rare |
| Registry writer = hub-only | ✅ acceptable | Concentre l'autorité, évite les races. SPOF assumé — cohérent avec M₁ single-host actuel |
| Axial N×M (router = 1 stage partagé) | ✅ juste | Pattern prouvé (ADR-073, typing factory), gardé par importlinter |
| Cortex = satellite à couplage mince | ✅ bon design | 1 seul subject NATS (`roxabi.memory.query.assemble`), pas d'import partagé, pas d'accès DB direct |
| Spike #1807 | ✅ bien construit | 6 critères GREEN mesurables, fallback RED défini, timebox 2-3j |

---

## 2. Drift vision↔code (vérifié source)

| Claim vision | Réalité code | Verdict |
|---|---|---|
| clipool = « `claude -p` over NATS » | `build_cmd()` construit `claude --input-format stream-json --output-format stream-json --verbose --model …` — **subprocess persistant par pool_id**, pas one-shot `-p` (`cli_protocol_types.py:57-67`, `cli_pool_spawn.py:150`) | **DRIFT** |
| « Bus de jobs JetStream ✅ substrat » | Dispatch clipool = NATS **request-reply** (`factory.clipool.cmd`), pas une queue JetStream. JetStream réel : turn-writer (`FACTORY_TURNS`), audio dedup, `factory-msg-index` KV | **PARTIAL** |
| « sessions KV » | ∄ — sessions CLI persistées en SQLite via TurnWriter | **DRIFT** |
| Subjects `factory.*` (rename clos) | ✅ propre — résidus `lyra.` = noms de loggers Python uniquement | OK |
| WorkerPoolClient 3-layer (#1278) | ✅ présent (`transport/worker_pool_client.py`) | OK |
| `factory.job.<id>.*` | ∄ — contrats encore sur `factory.results.<job_id>` / `factory.progress.<job_id>` (attendu, epic ouvert) | conforme |
| 8 containers Quadlet | ✅ exact (`deploy/quadlet.toml:5-44`) | OK |
| CLI `factory agent create` | Documenté mais `@agent_app.command(name="create")` introuvable | **PARTIAL** |

**Conséquences** :
- Le framing `-p` de la rupture runtime est faux. Le mode réel (stream-json bidirectionnel persistant) est **plus proche du RPC mode de pi** que d'un one-shot — bonne nouvelle pour la migration, mais toute analyse coût/migration basée sur la sémantique `-p` est à recadrer. La menace cliff tient probablement (stream-json = interface programmatique), confiance haute mais non vérifiée contre l'annonce Anthropic exacte.
- Corriger 3 lignes de la vision (substrat ⚠ : « dispatch queue », « sessions KV », « claude -p »).

---

## 3. Asymétries du substrat en prod

Le « ✅ substrat déjà en prod » de la vision masque des trous de fiabilité **sur le chemin principal** :

| Trou | Evidence | Impact |
|---|---|---|
| `stream()` hors Protocol (duck-typed `hasattr`) → **zéro circuit-breaker, zéro retry** sur le chemin streaming ; le non-streaming a 3 décorateurs | `llm-streaming.md:58` | Le chemin user-visible majoritaire est le moins protégé |
| Dual-write turns : adapters publient `factory.turns.write` **directement** en plus du turn-writer durable ; « α-deviation tolérée » sans borne ni dédup documentée | `CURRENT.generated.md:100,127` | Ordre/dédup non garantis sur la donnée la plus précieuse (l'historique) |
| `ProcessorRegistry.post()` invoqué **uniquement** en branche non-streaming → commandes silencieusement perdues en streaming ; singleton module-level dans un système DI-first | `workers-tooling.md:94,121` | Bug silencieux ; contredit ADR-022/025 |
| WorkerRegistry sans fallback queue-group : worker mort = 15 s de routage à vide (TTL 15 s / heartbeat 5 s) | `contracts.md` | Fenêtre de non-réponse voice/LLM |
| `OutboundDispatcher._queue` unbounded — contredit l'invariant #6 (`maxsize=100`) | `adapters.md` open gaps | Accumulation mémoire sous backpressure |
| `smart_routing` : code présent, `enabled=true` **rejeté par le validateur** — dead code dans la chaîne de décorateurs documentée | `workers-tooling.md:90` | Charge cognitive, modèle mental faux |

→ Aucun n'est bloquant pour la slice, mais #1 et #3 méritent des issues **avant** d'augmenter le volume (CM auto = plus de streaming, plus de commandes).

---

## 4. Job model — design vs séquencement

### Design : solide, avec réserves localisées

Les chaînes de décision tiennent (alternatives réellement rejetées avec raisons — Option C WorkEnvelope, refus JetStream-results, typed enum vs rule engine). Réserves :

- **N2-N6 admis non résolus** (ordering steer, backpressure, ACL steer, propagation job_id→rule-engine, query « quels jobs pour ce user »). N2/N4 doivent être tranchés **avant** #1799, pas pendant.
- **« design verrouillé » est sur-vendu** : ADR-084 (ratifié 06-08) nécessite déjà un amendement (#1794) ; le turn n'a pas d'identité (`message_id` réutilisé comme trace) — gap admis. Verrouillé ≈ stable-pour-l'instant.
- `composite_depth ≤ 3` : arbitraire mais inoffensif (guard anti-récursion, déjà shippé dans `jobs/models.py`).

### Couplage au harness : le cœur survit, la périphérie casse

Si le spike #1807 est GREEN, ce qui casse est **circonscrit au domaine `cli`** :

| Composant | Si swap pi/omp |
|---|---|
| WorkEnvelope, `job_id=run`, registry, taxonomie, 3 tiers | **SURVIT** (transport-agnostic) |
| Tool-system D1-D16, ToolResult, RemoteTool | **SURVIT** |
| `CliControlCmd`/`CliCmdPayload`, `cli_session_id`, `resume_and_reset`, arbre F2, contrainte cwd | **CASSE** — ne pas porter, re-designer le contrat worker frais |
| Opacité des hops internes (§15.1) | **SURVIT SSI discipline** : omp émet des events granulaires (turn-level) — les mapper sur `progress`, **jamais** sur des sub-jobs, sinon le job tree devient ambigu. Guardrail à écrire dans job-model.md le jour du swap |

### Séquencement : l'inversion

```
Vision (ordre recommandé)     Backlog réel (issues ouvertes)
─────────────────────────     ──────────────────────────────
0. Décision G                 #1793-#1800 + #1778 + #1619 + #1792
1. Slice fine HITL            = control plane Shape D complet
2. Mesurer reach              (steer, registry, router, dashboard)
3. PUIS généraliser           
                              Issues slice : AUCUNE
```

La slice (cron scan → 1 post → HITL → publish X) est un job **Shape B stateless** : elle a besoin de dispatch + result + un tool publish. Elle n'a besoin ni du steer, ni du registry, ni du router, ni du dashboard. L'epic #1792 est la **généralisation** que la vision dit de faire *après* mesure du reach. C'est exactement le « framework générique avant la 1ʳᵉ valeur » que le doc dénonce (§ Ordre d'exécution).

**Tri proposé sur #1792 et ses feuilles** :

| Issue | Action | Pourquoi |
|---|---|---|
| #1793 (taxonomie subjects) | ✅ faire | S, harness-agnostic, dette de nommage — moins cher maintenant que plus tard |
| #1794 (amend ADR-084) | ✅ faire | docs-only, fige le `job_id=run` |
| #1795 (JobResult pub/sub) | ~ ok | utile à la slice (résultat du job de génération) |
| #1796-#1797-#1799-#1800 (registry, router, steer, dashboard) | ❄ **geler post-slice** | Shape D pur — aucune valeur pour la slice |
| #1798 (sub-jobs STT/LLM/TTS) | ❄ **geler post-spike** | re-plomberie transport dont la forme dépend du verdict harness |

---

## 5. Décision G — recomposition

### G = deux décisions indépendantes, pas une

```
G1. Substrat COÛT-MODÈLE  : où vont les tokens des workers ?   ← deadline réelle 06-15
G2. Substrat HARNESS      : quel runtime exécute les workers ? ← spike #1807, pas de deadline dure
```

La vision les fusionne (« runtime worker post-cliff »). Or :

- **G1 a un stopgap sans swap de harness** : `ANTHROPIC_BASE_URL` du claude CLI → LiteLLM proxy (`:18091`, déjà en prod) qui sert `/v1/messages` et route vers Kimi/DeepSeek/Featherless/self-host. À **valider en ~1 h** (caveat connu : routage thinking + User-Agent gating, cf. précédent Fireworks). Si ça marche, le cliff est neutralisé pour les workflows volumineux sans toucher clipool.
- **La slice n'est pas bloquée par G du tout** : 1-2 posts/jour HITL = volume trivial, largement sous le pool de crédits Agent SDK d'un tier Max même post-cliff. « G bloque la slice » (vision, Build order #0) est faux au volume de la slice — G bloque le **CM auto scale-out**, qui vient après la mesure du reach.

→ Reformuler la Décision G dans la vision en G1/G2, et **dé-bloquer la slice**.

### G2 — critique du spike + reco conditionnelle

Le gate GREEN de #1807 est bon (session lifecycle, event mapping, ToolHandler bridge, LiteLLM, steer latency, Bun-in-Quadlet). Trois ajouts :

1. **Stratégie bus-factor avant adoption, pas après** : oh-my-pi = fork solo (can1357), v15.10.8 taggé *aujourd'hui*, clone shallow non audité, addon Rust ~27k lignes, Bun-only. Si GREEN → pin exact + fork/vendor sous `Roxabi/` dès le jour 1, et décider la politique de suivi upstream. earendil-pi a le hardening supply-chain (shrinkwrap, pinned, audit CI) qu'omp n'affiche pas — c'est l'inverse de ce qu'on veut adopter les yeux fermés.
2. **Exécuter le fallback RED dans le même timebox** : 0,5 j pour prouver `earendil/pi --mode rpc` + shim OpenAI-compat → LiteLLM. Si omp meurt dans 6 mois, le coût de bascule est connu d'avance.
3. **Trancher Shape A vs Shape C au moment du verdict** : omp supporte les deux (sessions file-backed + `SessionManager.inMemory()`). Le harness spec penche stateless (hub tient l'historique, store canonique #640 OPEN). Stateless-first = supprime la classe de bugs cwd/resume (F2 disparaît entièrement) au prix du re-send d'historique. Recommandé : **Shape C stateless d'abord**, Shape A persistant seulement si la latence/le coût du re-send le justifie mesurablement.

**Reco conditionnelle** (sans préempter le spike) : omp est le seul candidat avec LiteLLM/xAI/Ollama natifs + MCP + client Python RPC — le fit fonctionnel est net. Le risque n'est pas la capacité, c'est la **gouvernance du fork**. GREEN ⇒ adopter avec vendoring ; RED ⇒ earendil-pi + shim (perdre MCP, garder l'essentiel) plutôt que thin loop custom (~200 lignes qui deviendront 2000).

---

## 6. Mémoire — l'angle manquant de la vision (cortex inclus)

### Trois systèmes mémoire coexistent, aucun arbitrage écrit

| Système | État | Contenu |
|---|---|---|
| L0-L4 factory (`storage.md`) | **code partiel** | L0 actif (Pool.history), L3 partiel (`memory.db` SQLite + BM25 + sqlite-vec, par user_id), L1/L2/L4 deferred |
| `docs/memory-system/` (11 fichiers, factory) | **spec, avril 2026, « ready for implementation »** | PostgreSQL/pgvector, knowledge graph, consolidation nocturne, decay Ebbinghaus |
| roxabi-cortex | **spec, 2026-05-06, 0 code** | DuckDB ×2 (insight lake + memory graph), hippo-decay, compiled truth, NATS-only (`roxabi.memory.query.assemble`) |

Les deux specs se recouvrent (~80 % : graph, decay, consolidation, compiled snapshots). Le harness spec a **déjà pré-tranché** : `MemoryInjection=harness` via roxabi-cortex (`1490-harness-design-spec.mdx`). Donc `docs/memory-system/` est de facto mort — mais un lecteur de la doc factory construit un modèle mental faux.

**Actions** :
1. ADR court : **cortex = SSoT mémoire long-terme**, `docs/memory-system/` archivé (ou explicitement re-scopé « évolution interne L3, supersedé par cortex »).
2. **ADR-009 (taxonomie d'entités) = LE chemin critique cortex** — c'est une session d'interview, zéro code, pas chère. Elle débloque : DATA-MODEL → `roxabi-contracts` `memory.py`/`insight.py` → toute implem. À planifier indépendamment de la slice.
3. **Namespace** : cortex publie en `roxabi.*` alors que factory vient de finir le rename `factory.*`. Cortex étant cross-projet (Claude Code, Lyra, mail…), `roxabi.*` est défendable — mais que ce soit une **décision écrite** (ADR + entrée acl-matrix), pas un drift. Le frame #1670 l'a exclu du rename « car pas encore peer NATS » ; ça expire le jour où cortex boote.
4. **Group chat / `user_id`** : la pool history est scope-scoped, la mémoire est user-scoped — en group chat, qui est crédité de l'écriture ? Non résolu dans les docs (`storage.md:72-84` vs `messaging.md` RoutingKey). Tolérable aujourd'hui ; **bloquant avant cortex Mode 2** (cortex SSoT du recall) : à trancher dans ADR-009/010.

### Le positionnement satellite est le bon

Couplage = 1 subject request-reply au context-build du harness. Pas d'import partagé, pas d'accès DuckDB depuis factory, modes de cohabitation (ADR-010) explicites. C'est conforme à la règle « toute nouvelle capacité = un adapter sur l'hexagone ». Seul point de vigilance : le **budget latence** de l'appel assemble (cible p95 < 200 ms côté cortex) doit entrer dans le SLA du harness, avec dégradation propre (turn sans mémoire ≻ turn bloqué).

---

## 7. Décisions E / C / F + tool-system

| Décision | Avis |
|---|---|
| **E — HITL** | Défaut juste. Implémentation la moins chère : l'approbation **dans le chat existant** (Telegram/Discord via adapters en prod) — zéro UI neuve, exerce le substrat, et le passage HITL→auto = un flag par lentille (Bouly quotidien d'abord, Roxabi gated ensuite). Critère de sortie à définir *avant* la mesure : « N posts approuvés sans édition sur M jours » sinon le HITL devient permanent par inertie |
| **C — Postiz** | Bloquée par une **contradiction interne non résolue** : D11 (493-articulation) dit bash-CLI `transport=stdio` depuis le hub ; le doc domain-nature dit « devrait être satellite NATS ». Trancher la contradiction d'abord — sinon C sera décidée deux fois. Vu la slice : stdio direct = suffisant pour v1, satellite = généralisation post-slice. La contradiction se dissout si on la formule **temporellement** (stdio v1 → satellite si volume) |
| **F — réseaux** | Correctement parquée. Un seul réseau (X) jusqu'à reach mesuré |
| **Tool-system (493)** | Design verrouillé D1-D16 mais **parké sans aucune issue d'amorçage** — un design sans issue rote (le code bouge dessous, cf. drift §2). Minimum : 1 issue « amorçage tools v1 » liée au verdict #1807 (le harness consomme le registre), ou un marqueur `parked-until:<trigger>` explicite dans l'analyse |

---

## 8. « Factory d'agents » générique — gaps réels

Le but énoncé : déployer facilement des agents quel que soit le besoin ultérieur. État :

| Brique | État | Gap |
|---|---|---|
| Provisioning agent/bot | ✅ bon | TOML seed → `agent init` → `assign` → secret → quadlet. DB-first (#1416), hot-reload par `updated_at`. Résidu : verbe `create` documenté-non-câblé |
| Per-agent container | 📐 designé | D2 (`lyra-agent@<slug>.container` template Quadlet) — pas construit ; nécessaire au « plusieurs agents isolés » |
| Harness (#1490) | 📐 designé | Le vrai goulot : c'est lui qui consomme tools + mémoire + jobs. Verdict #1807 = son fondement |
| Tool registry | 📐 designé, parké | cf. §7 |
| Mémoire | 📐 designé ×2 | cf. §6 — arbitrage requis |
| HA / multi-host | ⚠ assumé single-host | hub-only writer + M₁ = SPOF cohérent aujourd'hui ; P3 de la job-analysis (« forcing function multi-machine ») reste la bonne question à ne pas répondre prématurément |

Lecture : la généricité ne viendra **pas** de plus de control plane (Shape D) — elle viendra de harness + tools + mémoire. Les trois sont designés, zéro des trois n'est amorcé, et le backlog actif pousse le quatrième pilier (jobs runtime control) qui est le moins demandé par la slice.

---

## 9. Recommandations priorisées

| # | Action | Pourquoi | Quand |
|---|---|---|---|
| 1 | Valider stopgap G1 : claude CLI → `ANTHROPIC_BASE_URL` → LiteLLM (~1 h de test) | Neutralise le cliff sans dépendre du verdict harness | avant 06-15 |
| 2 | Exécuter spike #1807 (+ fallback earendil-pi testé, + plan vendoring si GREEN) | G2 sur faits ; bus-factor traité avant adoption | cette semaine |
| 3 | **Ouvrir les issues slice** (watcher cron, génération 2-lentilles, HITL chat, publish X stdio) et la shipper sur le substrat actuel, Shape B | C'est la valeur ; rien dans le backlog actuel ne la construit | immédiat |
| 4 | Re-trier #1792 : faire #1793/#1794 (+#1795 si la slice le veut), geler #1796-#1800, geler #1798 jusqu'au verdict spike | Anti « framework d'abord » ; évite de plomber sur un harness non tranché | immédiat |
| 5 | ADR mémoire : cortex = SSoT, archiver `docs/memory-system/` ; planifier l'interview ADR-009 ; trancher `user_id` group-chat + namespace `roxabi.*` | Trois specs concurrentes = dette de cohérence qui grossit | ≤ 2 sem |
| 6 | Corriger vision : G→G1/G2, slice dé-bloquée, « `claude -p` »→stream-json, « dispatch queue/sessions KV » → réalité | Le doc stratégique pilote les décisions — il doit être vrai | avec #4 |
| 7 | Issues fiabilité : `stream()` au Protocol + CB/retry streaming ; `ProcessorRegistry` sur chemin streaming ; borner le dual-write turns | Trous sur le chemin majoritaire, avant montée en volume CM | post-slice |
| 8 | Trancher la contradiction D11 (stdio v1 → satellite si volume) ; 1 issue d'amorçage tools liée au verdict #1807 | Décision C propre ; design parké ≠ design abandonné | post-spike |

---

## Annexe — sources des evidences

- Drift : `src/factory/core/cli/protocol/cli_protocol_types.py:57-67`, `cli_pool_spawn.py:150`, `packages/roxabi-contracts/src/roxabi_contracts/jobs/subjects.py:41-49`, `deploy/quadlet.toml:5-44`
- Asymétries : `docs/architecture/llm-streaming.md:58`, `workers-tooling.md:90,94,121`, `CURRENT.generated.md:100,127`, `adapters.md` (open gaps), `.importlinter:11-138`
- Job model : `docs/architecture/adr/084-workenvelope-job-id-invariant.mdx:39-112`, `artifacts/analyses/job-model-concept-analysis.md` §4, §11 F2, §12 (U/P series), §13 (D-3/D-5a/D-8), §15
- Tools : `artifacts/analyses/493-tool-system-articulation-analysis.mdx` (D1-D16, §12 Q1-Q8), `493-tool-domain-nature-consolidation.mdx` §2.5
- Runtime : `~/projects/docs/research-claude-p-alternatives-2026-06-08.md`, `external_repos/{pi-mono,earendil-pi,oh-my-pi}` (providers, rpc-types, print-mode), issue #1807 (gate GREEN/RED)
- Cortex : `roxabi-cortex/docs/ARCHITECTURE.md`, `docs/adr/ADR-004/006/009/010/011`, `artifacts/specs/spec-cortex-memory.md`, `roxabi-factory/artifacts/specs/1490-harness-design-spec.mdx`, `artifacts/analyses/harness-epic-consolidated.md` §3.2
- Issues : gh `Roxabi/roxabi-factory` #1044, #1203, #1619, #1622 (closed), #1778, #1792-#1800, #1807 (état au 2026-06-09)
