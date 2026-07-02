# ADR Consolidation — wave 3 (dédoublonnage ADR↔ADR + index + titres) — 2026-07-02

> Suite de `adr-consolidation-2026-07.claude.md` (waves A/B/C = absorption ADR→domain pages, PRs #2133/#2134/#2140/#2141/#2146). Cette passe répond à une question différente : **le SET des 46 ADR actifs est-il lui-même redondant ?** Méthode : 5 lecteurs indépendants par tranche thématique, lecture intégrale, mandat « refuser les fusions forcées » ; chaque hypothèse acceptée OU réfutée avec citations de sections.

## Verdict global

46 actifs → cible **~34-35** (−11 fort, −2 optionnel), via fusion « un niveau au-dessus » quand plusieurs ADR racontent LA MÊME décision à des époques/zooms différents. Le reste du set est sain : **13 hypothèses de fusion ont été réfutées** avec preuves — le problème n'est pas généralisé, il est concentré sur 3 lignées (streaming, dérivation ACL, cluster observabilité) + 5 pseudo-décisions.

## 1. Fusions recommandées (fort)

| # | Fusion | Nouveau record (titre proposé) | Preuve clé | Net |
|---|---|---|---|---|
| F1 | 028+032+070 → **nouveau** | "Typed LLM streaming pipeline — `LlmEvent → StreamProcessor → RenderEvent`, v1→v2 (AG-UI-superset)" | 070 frontmatter « Extends ADR-032 » ; 032 bâtit sur 028 ; une lignée v0→v1→v2. Survivants : contrat hexagonal (032), vocabulaire v2 (070), 3 edge-cases CLI (028) en annexe | −2 |
| F2 | 036+072 → **nouveau** | "RenderEvent NATS wire protocol — seq-framed chunk envelope + codec-registry dispatch" | 072 `supersedes: ADR-032 (v1 wire shape)` ; 036 §Amendment défère son enum à 072. Modèle (F1) vs wire (F2) = frontière honnête entre llm-streaming.md et messaging.md | −1 |
| F3 | 097 → **092** | "Observability engine architecture — headless engines, split transport, trace v1 = otel-raw" | 092 §6 s'intitule déjà « Amendment (ADR-097) » et porte le contenu ; 097 §Context s'auto-décrit « This ADR fixes the v1 sink » = amendement déguisé en ADR | −1 |
| F4 | 064 → **046** (retitré) | "NATS access artifacts (auth.conf + grants) are derived from `acl-matrix.json`, never hand-written" | 046 Invariant 1 (« pure render ») ; 064 Option B = ce même invariant appliqué à une classe de grants. Même SSoT, même générateur, même principe | −1 |
| F5 | 074 → **055** (carve-out) | pair du carve-out 085 déjà inliné dans 055 | 074 §Consequences « Fits ADR-055 Quadlet credential-store pattern… no second credential mechanism ». **Résidu à préserver** : suppression CredentialStore/LyraKeyring/bot_secrets + résolution χ-1 (secrets unitaires ≠ blob JSON) | −1 |
| F6 | 095 → archive | contrat déjà dans messaging.md | pseudo-décision : 1,9 KB, zéro alternative pesée ; « mirror llmCLI » = application d'un pattern existant | −1 |
| F7 | 058 → archive | principe déjà dans engineering-standards.md §User-visible error contract | §Amendment : Option B (LyraUserError/ErrorBoundaryMiddleware/NullMessageManager) « never built… zero occurrences ». Titre actuellement MENSONGER (mécanisme promis inexistant) | −1 |
| F8 | 019 → archive | invariant déjà dans workers-tooling.md §Key invariants | double why mort : SmartRoutingDecorator supprimé ET l'isolation i18n per-agent n'est pas le comportement réel (MessageManager partagé) | −1 |
| F9 | 061 → archive | état vivant dans workers-tooling.md §Importlinter | work-log terminé−1 sous 059 ; résidu SessionToolsProtocol → issue/debt, pas un ADR. Mettre à jour la phrase « ADR-061 remains live » dans 059 §Supersedes | −1 |
| F10 | 057 → archive | config stream + SecurityEvent → security-routing.md | sa seule vraie décision (placement Option B du port) a été **reversée** par son amendement ; le reste = notes de câblage | −1 |
| F11 | 042 → archive + **nouveau ADR** "Runtime brand tokens & marks are canonical in-repo (`brand/`); exploration brand lives in forge" | décision inversée = mauvais record à garder actif | garder la décision positive que le repo possède, au lieu de l'enterrer dans un ADR qui affirme l'inverse ; la moitié forge reste hors périmètre factory | 0 |

**Optionnels (modéré, à trancher) :**
- O1 : 035 → 076 ("NATS subject grammar + three-plane model") — les whys se *superposent* (grammaire → taxonomie) plus qu'ils ne se répètent ; défendable dans les 2 sens. Net −1.
- O2 : 098 → 094 — 098 `amends:[094]`, mais son cœur est une décision plane-④ (read model) distincte de « 1 container ou 2 ». Caveat honnête du lecteur : garder 098 est défendable. Net −1.

## 2. Fusions RÉFUTÉES (ne pas retenter)

| Paire | Raison (citée) |
|---|---|
| 075+078 | axes orthogonaux : gouvernance+déploiement (075, panel) vs inversion de dépendance typée (078) ; 078 exclut explicitement le write-path |
| 008+087 | 008 porte un why indépendant (Polymarket −67 %, prompt-caching) ; 087 = arbitrage d'ownership, pas de périmètre |
| 068 parent de 067/075 | 068 = axiome réel (α/β + critère de choix) ; fusionner les instances produirait un monstre illisible |
| 045+049 | 049 Option B (fold dans roxabi-nats) explicitement **rejetée** — la séparation EST la décision |
| 051 dans F4 | 051 = least-privilege blast-radius (consommateur de la dérivation), pas la dérivation elle-même |
| 091+092 | 092 § : « ADR-091 answers *what kinds of signal exist*. This ADR answers *which engines…* » — ontologie vs réalisation |
| 093, 096 | 093 = audit opérateur (≠ archi obs, frontière explicite vs 057) ; 096 = registre de connecteurs (candidat à SORTIR du domaine obs) |
| 058+089 | 089 liste 058 « Orthogonal — not replaced » ; domaines de catch différents (LLM soft errors vs pre-hub) |
| 001+084, 079+095, 065, 059+061-fusion | questions distinctes documentées dans chaque rapport |

## 3. Index : recommandation = SUPPRIMER meta.json

Constat :
- Consommateurs réels de `adr/meta.json` : 1 lien dans ARCHITECTURE.md + `tools/adr_consolidate.py` (qui le reconstruit destructivement). `docs.framework: none` → aucun renderer Fumadocs. Aucun gate ne le valide ; il a dérivé (4 entrées manquantes) en 3 semaines.
- **L'index utile existe déjà ailleurs** : chaque domain page porte une table « ADR archive » (status + résumé 1 ligne) — index contextuel, maintenu là où vit le contexte, avec PLUS d'info que meta.json.

Décision proposée : supprimer `adr/meta.json` + `adr/archive/meta.json` ; repointer la phrase d'ARCHITECTURE.md vers les tables des domain pages ; amender ADR-086 d'une ligne ; mettre à jour `~/projects/ssot/conventions.ssot.md` (la ligne « meta.json par domaine, séparateurs Fumadocs ») ; `adr_consolidate.py` devient sans objet (déjà noté destructif). Si Fumadocs revient un jour : générer meta.json depuis un champ frontmatter `domain:`, jamais à la main.

Conséquence assumée (= la demande) : **le titre devient le contrat.** Convention : slug = la décision en assertion (`NNN-<decision-as-statement>`), frontmatter `title: "ADR-NNN: <décision, pas le sujet>"`. Test : un lecteur sans index doit savoir ce qui a été décidé sans ouvrir le fichier.

## 4. Titres — audit (46)

- **MISLEADING (2)** : 042 (affirme l'inverse de la vérité actuelle — résolu par F11), 058 (mécanisme promis jamais construit — résolu par F7). 074 incomplet (cache la vraie décision : suppression du layer DB — moot si F5).
- **VAGUE (8)** : 008 (`phase1-memory-scope` → `memory-implement-levels-0-3-only`), 068 (`ecosystem-service-plane` → « Shared state must be NATS-mediated and host-invisible (α/β) »), 035 (« …Convention » → « NATS subjects use a domain-first hierarchy »), 036 (moot si F2), 079 (« axial consolidation » = jargon → « Hub sole provisioner of the audio stream; adapter grants via shared ACL group »), 091 (dé-brander « Sentinelle » → « Four observability planes — signal taxonomy & boundary rules »), 092 (slug `observability-architecture` → `headless-engines-split-transport`), 019/071/075 (mineurs/moot).
- **Format** : 064 et 072 sans préfixe `ADR-0XX:` dans le title.
- Le reste (~33) : CLEAR.

## 5. Hygiène découverte en passant

- 089 : banner ancre cassée `engineering-standards.md#typed-error-boundary` (aucune ancre dans le fichier) → pointer `### User-visible error contract`.
- 083 : décision réelle, mais tracking S1-S7 + « Update (2026-05-31) » = plan roulant → re-homer vers issues (#1537/#1277), garder la décision.
- 067 : §Auth plane Phase 2 = design spéculatif sans issue (« no issue filed yet — this section is the design record ») → extraire vers artifacts/specs.
- 075/067 : corps encore en `src/lyra/…`/`lyra-*.container` (le rename a été amendé ailleurs mais pas dans ces deux corps).
- Collision de numéro 068 (actif ecosystem-service-plane vs archive selinux) — même cas toléré que 017×2 en archive ; à surveiller si renumérotation un jour.
- 052 : classé contracts mais décision routing/NATS — re-home possible de son banner.

## 6. Exécution proposée (wave 3, si GO)

| Lot | Contenu | Effort |
|---|---|---|
| W3-a | F6-F10 archives simples + fix ancre 089 + phrase 059→061 + re-home tracking 083 + extraction 067 Phase-2 | S |
| W3-b | F1-F5 fusions (2 nouveaux records + retitrage 046 + carve-outs 055) + F11 (nouvel ADR brand) + O1/O2 si retenus | F-lite |
| W3-c | suppression meta.json ×2 + ARCHITECTURE.md + amendement 086 + renames de titres/slugs (avec sweep des liens) + conventions.ssot.md (repo projects-meta) | S/F-lite |

Post-wave-3 : ~34 ADR actifs (~63 archivés), zéro index à maintenir, titres = contrat.
