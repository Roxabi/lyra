# ADR Consolidation v2 — topo & matrice (2026-07-01)

> Analyse multi-agents (24 agents : 12 clusters de lecture + xref PR/issues + xref gates/outillage + audit domain pages, puis vérification adversariale de chaque candidat archive/merge). HEAD `b347140b` (staging). Consolidation v1 : 2026-05-09 (46 ADR → 9 domain pages, `artifacts/analyses/archive/adr-consolidation-matrix.md`).

## Constat

| Métrique | v1 (2026-05-09) | Aujourd'hui | Convention |
|---|---|---|---|
| ADR actifs | 46 → ~50 | **74** (12 778 lignes) | archive de traçabilité, ¬SSoT |
| ADR archivés | 26 | 24 (`ARCHITECTURE.md` dit 67/26 — faux) | — |
| Domain pages | 9 | **15** | ≤9, 1/cluster |
| Pages > 350 lignes | 0 | 2 (`voice-to-voice-analysis` 833, `messaging` 376) | ≤350 |
| Cluster sans page | 0 | **observabilité/control-plane : 7 ADR, 0 page** | 1 page/cluster |

Dérives mécaniques : doublon **097** (otel-raw vs pr-pipeline), 4 ADR absents de `adr/meta.json` (088, 094, 095, 097-pr), banners « Current truth » manquants (042, 084, 094, 097×2), xref pendante `deployment.md:64` → table 10-stages disparue d'ARCHITECTURE.md, doublon latent `017-*` dans archive/.

Le modèle ADR-086 (2-tier SSoT : pages = intent, adr/ = archive, gate doc-drift) **tient** — il s'est juste effrité sur les ~28 ADR ajoutés depuis v1, et le cluster observabilité est né sans page.

## Matrice de reclassement (74 ADR)

Verdicts issus de la 2ᵉ passe complète, chaque `archive`/`merge` vérifié adversarialement. **COND** = archivable seulement après migration préalable (voir § Conditions).

### Archive → `adr/archive/` (24)

| ADR | Note |
|---|---|
| 002 | 4 invariants hub dans messaging.md + code ; cite des chemins `src/lyra/` morts |
| 003, 015, 020, 023, 039 | implémentés, 100 % absorbés par adapters.md |
| 013 | décision **morte** (try/finally au call-site STT) — remplacée par read+unlink / PendingAttachment (ADR-083) |
| 014 | InboundAudio→AudioPayload résolu ; la 3ᵉ plateforme (web) a shippé |
| 004, 005, 006, 007, 026, 030, 031, 038 | implémentés, absorbés par workers-tooling.md |
| 022, 063 | DI event-bus / teardown bootstrap absorbés par storage.md |
| 069 | posture warn absorbée par security-routing.md |
| 009 | GENERIC_ERROR_REPLY absorbé par architecture-patterns.md |
| 080, 081 | déjà `superseded by ADR-086` ; **081 COND** |
| 077 | contrat opérationnel dans messaging.md ; **COND** |
| 088 | absorbé par messaging.md §FACTORY_JOBS + `infrastructure/jobs/AGENTS.md` |

2ᵉ vague optionnelle (les 2 passes divergent — contenu couvert par la page, seul le « why » retiendrait le statut actif) : **024, 029, 082** (storage). Si retenue : 74 → 46 actifs.

### Merge (1)

| ADR | Cible | Note |
|---|---|---|
| 085 | → ADR-055 (carve-out D5) | invariant bind-mount SIGHUP écrit 4× (055:453, deployment.md:250, deploy/AGENTS.md, unit comments) — fold, rien d'unique |

### Amend (18) — décision vivante, contenu partiellement faux

| ADR | Correctif |
|---|---|
| 073 | réécrire `src/lyra/`→`src/factory/` ; **supprimer la table 30 sites file:line** (INVENTORY interdit par 086) → garder l'invariant burn-down + `make quality-debt-report` ; gate = scripts/qg (pas pre-commit) ; banner → adapters.md |
| 035 | préfixe `lyra.*`→`factory.*` ; contrainte bot_id numérique fausse depuis Platform.WEB |
| 036 | taxonomie v1 + « exactly one text chunk/turn » supersedés par 072/v2 (TextDelta incrémental) — l'enveloppe/seq-gap restent vivants |
| 032 | contrat hexagonal vivant ; rafraîchir refs, garder le rejet dual-channel (unique ici) |
| 072 | vivant mais mal classé (codec = wire concern) ; banner/cluster → messaging |
| 008 | « defer L1/L2 in factory » dépassé par 087 (→ cortex) — note de pointeur |
| 068 | ligne TurnStore « direct-write toléré jusqu'à #1331 » : #1331 a shippé (TurnPublisher→factory-turn-writer) |
| 075 | status « superseded by ADR-078 » **FAUX** — le writer sublayer est l'archi qui tourne ; 078 = read-path distinct. Corriger status + frontmatter |
| 046 | `gen-nkeys.sh` mort → Python `factory-acl`/`scripts/gen_nkeys.py` ; IDENTITIES = map `acl-matrix.json` v4 ; `factory ops verify` shippé |
| 051 | `_INBOX.<identity>` majuscule supersedé → lowercase + `nats_connect(identity_name=…)` + gate CI ; roster stale |
| 057 | placement Option-B **reversé** : port désormais dans `core/ports/audit_sink` |
| 055 | ancre Quadlet (garde) ; D2 faux : consolidation big-bang sur factory-nats:4222, plus de ports 4223/4224 ; absorbe 085 |
| 049 | placement fakes/guards + 2 sous-décisions fausses sans note d'amendement |
| 058 | Option B (NullMessageManager) jamais construite — marquer not-implemented → mécanismes réels (DROP-guard SttMiddleware) |
| 010 | install-wrap-declare vivant ; Layer-3 « Phase-2 MCP » dépassé par tool dispatcher/satellites → pointer tool-architecture.md |
| 019 | invariant shared-pool vivant ; rationale SmartRoutingDecorator **mort** (classe supprimée) |
| 061 | plan largement livré (compte d'exemptions 1, pas 4) ; reste SessionToolsProtocol |
| 042 | « no brand/ in any repo » **FAUX** — `brand/` (12 fichiers tokens/SVG) est git-tracké ; amender le périmètre |

### Fix-metadata (3) + hygiène

| Item | Fix |
|---|---|
| 084 | banner manquant ; mal classé MSG → job-model.md |
| 094 | banner + entrée meta.json (ADR ancre du dashboard, invisible dans l'index) |
| 097-pr-pipeline | **renuméroter 098** + banner + meta.json (097 reste à otel-raw, cité par 092/094) |
| meta.json | ajouter 088 (si non archivé), 094, 095, 098 |
| ARCHITECTURE.md | compteurs (67/26 → réels post-lot), table 15 pages → cible, xref « 10-stage table » pendante |

### Keep (28)

001, 065, 076, 079 (messaging) · 028, 070 (llm) · 083 (adapters, en cours S3–S7) · 024, 029, 067, 078, 082, 087 (storage) · 064, 090 (security) · 074 (deploy) · 045, 052 (contracts) · 059, 089 (eng) · 091, 092, 093, 096, 097-otel (obs) · 071 (workers) · 095 (voice) · 086 (foundational, seul SSoT du modèle 2-tier).

## Conditions préalables (vérification adversariale)

1. **077** : sa « forward-compatible constraint » (tout futur média durable ⇒ famille de sujets 5-tokens distincte + durable consumer distinct + ¬collapse sur le path texte 4-tokens) n'existe **nulle part ailleurs** — migrer dans messaging.md (ou corps de 079) + repointer `docs/runbooks/outbound-audio-deploy.md:3` vers 079. Ensuite archivable.
2. **081** : porter la contrainte « baseline falsifiée avant baselining » dans ADR-086 + repointer la citation `scripts/check_subject_literals.py:12` (ADR-081→086). Ensuite archivable.

## Domain pages : 15 → 11 hand-authored (+ CURRENT.generated)

| Page | Verdict |
|---|---|
| voice-to-voice-analysis (833) | **sortir** → `artifacts/analyses/archive/voice-to-voice-models-sota.md` (artefact recherche 2026-03, ère supervisord ; le voice current-truth vit déjà dans adapters/messaging/contracts) ; corriger la ligne ARCHITECTURE.md qui le décrit faussement |
| target-architecture (347) | **dissoudre** : spec streaming (≈80 %) → llm-streaming ; doctrine hexagonale (≈20 %) → architecture-patterns |
| tool-architecture (120) | **merger** → workers-tooling (284 lignes combinées, sous le cap) |
| testing-conventions (78) | **merger** → architecture-patterns, renommée **`engineering-standards.md`** (doctrine cross-repo, ¬page domaine) ; trim contenu pédagogique pour ≤350 |
| messaging (376) | **rééquilibrer** : chunk protocol + codec → llm-streaming ; transport layer (SDK roxabi-nats) → contracts ; lands ~300 |
| observability | **créer** — promouvoir `docs/OBSERVABILITY.md` (199 l., de facto page échouée à la racine, top self-contradictoire « no OTel » vs §factory-otel) → `docs/architecture/observability.md`, étendue control-plane/094 + sentinelle/091 + audit/093 + otel/097. Pas de page dashboard séparée (094 consolide le dashboard DANS le control-plane) |
| adapters, storage, security-routing, job-model, deployment, contracts, llm-streaming, workers-tooling | **garder** (deployment/contracts/job-model à rafraîchir, cf. drift) |

Résultat : **9 pages domaine** (messaging, llm-streaming, adapters, storage, security-routing, deployment, contracts, workers-tooling, job-model) + observability (10ᵉ, nouveau cluster réel) + engineering-standards (doctrine, hors décompte domaine). Ne PAS forcer 9 en fusionnant contracts→messaging ou job-model→messaging : chaque combinaison crève le cap 350, plus structurant que le cap 9.

## Drift de contenu prioritaire (xref 80 PR mergées + issues)

1. **deployment.md** — la plus stale à fort trafic malgré stamp 2026-07-01 : stages trust supprimés (#2121) encore listés ; 3 unités cloud-gateway absentes (26 composants vs « 23 ») ; D5 décrit auto-update.timer alors que quadlet-sync auto-converge chaque HEAD staging, désormais tiéré par classe de drift (#2126, e98cc805) ; content-addressing (#2120) et role-guard (#2117) absents.
2. **job-model.md** — aucun concept *cancel* alors que #2123 a shippé le 1er runtime control live ; table implementation-status périmée (#1793/94/96/1800 fermés, false-done connu sur 1796/1800) ; re-scoper #1799 avant implémentation.
3. **contracts.md** (05-09) — cite roxabi-vault (déprécié) ; ignore `roxabi-satellite` (#2024), `roxabi-obs`/`roxabi-otel` (#2065/#2072) ; ADR-045 à amender ou nouvel ADR frontière satellite.
4. **testing-conventions.md** — zéro mention `scripts/qg`/stack.yml `quality_gates` (l'archi QG entière a bougé, #2076-#2080).
5. **storage.md** (05-24) — section mémoire à réécrire autour de 087 (cortex) ; schéma auth.db users.email/grants (#2002/#2082).
6. **messaging.md** — famille `factory.dashboard.*` quasi absente ; relocation sujets clipool (#2023), queue-groups SSoT (#2026).
7. **ADR-090** — drift inverse : « remove legacy trust stages » décrit comme futur, shippé (#2121) → note amended-by.
8. Issues : **#1054** partiellement supersedée (re-scoper fleet-only ou fermer) ; #1938 vs #2044, #1933/#1928 vs #2018-#2020 à re-scoper ; #2109 = décision Astryx à acter par ADR quand tranchée ; #669/#1623 à vérifier vs ADR-097 shippé.

## Contraintes gates/outillage (pour l'exécution)

- **Baseline doc-drift vide** → zéro coussin. Le risque CI n°1 : copier du texte d'ADR (zone exempte) vers une page (zone gated) — symboles morts/`lyra`-era ⇒ rouge. Curer au niveau intent, dé-backticker les symboles morts, annotations historiques sinon.
- Les moves `adr/` → `adr/archive/` sont **invisibles** aux 3 gates (doc_drift exclut tout le sous-arbre adr/, semantic idem, architecture_snapshot indifférent). Rien ne valide les meta.json ni la profondeur des banners (`../` → `../../`) — sweep manuel obligatoire (précédent : `archive/043` utilise déjà `../../`).
- **`tools/adr_consolidate.py` : ne PAS relancer tel quel** — il reconstruit `adr/meta.json` depuis la matrice 2026-05 (perdrait tout ≥072 + sections Foundational/Observability) et écrase les `status:`. Pour le réutiliser : nouvelles lignes matrice 072-098, domaines OBS/JOB à ajouter à `DOMAIN_TO_DOC`, et re-keyer par stem (le doublon 097 rend `find_adr_file("097")` non-déterministe). Sinon : édition à la main, le volume (24 moves) reste raisonnable.
- Suppression/renommage de page = rot de liens **silencieux** (les liens md ne sont pas scannés) → laisser des stubs de redirection pour target-architecture/tool-architecture/testing-conventions, grep-sweep des références.
- Gates à lancer localement avant push : `python3 tools/check_doc_drift.py` + `tools/check_doc_semantic_drift.py` (bundle CI-only, pas pre-push).

## Exécution proposée (3 lots, PRs séparées)

| Lot | Contenu | Effort |
|---|---|---|
| **A — hygiène mécanique** | renumérotation 098 ; meta.json ×2 ; banners manquants ; compteurs/table+xref ARCHITECTURE.md ; correctif status 075 | S |
| **B — archives + amendements** | 2 migrations préalables (077, 081) ; 24 moves → archive/ (+ banners `../../`) ; merge 085→055 ; 18 amendements (notes courtes, pas de réécritures lourdes) ; matrice v2 committée | F-lite |
| **C — pages** | créer observability.md ; sortir voice-to-voice ; dissoudre target-architecture ; merges tool-architecture/testing-conventions ; rééquilibrage messaging→llm-streaming/contracts ; refresh deployment/job-model/contracts prioritaires | F-full |

Post-lots : ~47-50 ADR actifs (~-35 %), 11 pages (9 domaine + observability + standards), 0 cluster sans page, index/compteurs exacts. Le trigger de re-consolidation de `~/projects/ssot/conventions.ssot.md` reste valable ; envisager d'y noter « re-run à +20 ADR post-consolidation ».

## Décisions opérateur — ACTÉES 2026-07-01

1. **2ᵉ vague incluse** : 024/029/082 → archive au lot B. Cible : **46 ADR actifs**.
2. **Recompte** : 9 pages domaine + `observability.md` (10ᵉ, cluster réel) + `engineering-standards.md` (doctrine cross-repo, hors décompte).
3. **042 inversé** : `brand/` in-repo (tokens + marks, consommé par le theme Astryx) = **modèle canonique**. Amender 042 pour superséder l'invariant « no brand/ in any repo » ; la partie exploration-assets→forge reste valable. Follow-up cross-repo (hors périmètre factory) : aligner les autres repos qui n'ont pas de `brand/` ou l'ont ailleurs + mettre à jour `~/projects/docs/brand-canon.md`.
4. **Hybride** : C1 = remaniement structurel (1 PR) ; C2 (deployment.md) et C3 (job-model.md) = PRs indépendantes, peuvent précéder C1.
