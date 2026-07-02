# CI/CD optimization audit — 2026-07-01/02

20-agent workflow audit (7 axes + 12 adversarial verifications + completeness critic), all numbers
measured on live runs (reference: staging run 28546524122) or reproduced locally at staging tip b347140b.

## TL;DR

| Question | Answer |
|---|---|
| Pourquoi la CI est longue ? | 3 gaspillages mesurés : un test pytest qui dort **90s** sur un lock SQLite, vitest qui ré-exécute le barrel phosphor-icons 35× (**102s** de setup sur 140s), et chaque suite intégration/e2e exécutée **2-3×** par run |
| Pourquoi tant de CI rouges ? | **Aucun hook local n'a jamais été installé** (`pre-commit install` refuse dès que `core.hooksPath` est set — état identique dans roxabi-plugins/live/llmCLI/voiceCLI) + 13 PRs dependabot zombies re-CI'd à chaque merge (**67,7 % de TOUS les runs**) + 1 test flaky + 1 course de publication omp-base (11 rouges en 2 min) |
| Gain de ce lot | CI wall-clock **7m07 → ~4m50** (−32 %), pytest local **100s → ~10s**, hooks locaux **actifs**, fan-out dependabot structurellement coupé |

## 1. État mesuré (avant)

Chemin critique PR = job `ci` 6m07 + `integration` sérialisé (+59s) = **7m07**. `docker-build` (2m47) parallèle.

| Step (job ci) | Durée | Détail |
|---|---|---|
| Setup (checkout→yq) | 24s | uv cache HIT (setup-uv v8 auto), bun 3.6s — rien à gagner |
| Quality gates (36 gates) | 94s | `dashboard_unit_test` **50s** (vitest: setup 102s/140s cumulés = barrel phosphor ré-exécuté par fichier), `typecheck` pyright 22s, ~30 gates <1,5s |
| Dashboard e2e | 34s | dont **12s** download chromium 291 MiB à chaque run (aucun cache) ; le Chrome complet 175 MiB n'est jamais exécuté (les tests utilisent headless-shell) |
| Coverage — factory | **166s** | 5468 tests, 4 workers xdist. Dont : **90,2s** de stall WAL (1 test, 3×30s busy_timeout, queue 98→100 % avec 3 workers idle = 38s de wall perdu) + re-exécution de tests/integration (96 tests, déjà joués 2× dans le job integration) + tests/e2e (déjà joués dans leur step) |
| Coverage — nats/contracts/obs | 41s | queue sérielle en fin de job |
| integration ×2 (matrice) | 57s | matrice `typing_enabled` **comportementalement inerte** : flag lu par 0 test d'intégration, publisher jamais câblé dans ces hubs (les 2 variantes exécutent le même code) ; branche `false` couverte en unit |

**Stall WAL (racine produit, pas test)** : `AuthStore._cleanup_bare_ids` laisse une transaction DELETE implicite ouverte quand 0 ligne supprimée (`commit` sous `if deleted:`) → read-mark WAL épinglé → chaque `wal_checkpoint(TRUNCATE)` des 3 autres connexions attend le `busy_timeout=30s` plein. En prod : les checkpoints périodiques des stores auth.db stallent 30s et abandonnent tant que le hub tourne.

## 2. Forensique des 18 CI rouges (24 h)

| Catégorie | Runs | Détail | Évitable localement ? |
|---|---|---|---|
| Course omp-base (infra) | **11** (61 %) | pin `factory-omp-base:16.2.12` mergé 17:50:33, tag GHCR publié 17:52:46 → tous les docker-build dans la fenêtre = `not found` ; auto-résolu sans changement de code | Non (ordering publish) |
| Hooks jamais installés | 2 | biome format + ruff sur feat/1773, gates `lint_js`/`lint` déjà câblés pre-commit — jamais exécutés | **Oui** (install hooks) |
| doc_drift caught late | 2 | feat/2114 : même erreur poussée 2× (`factory.core.admin` supprimé, réf doc) — gate CI-only, invisible localement | **Oui** (doc_drift → pre-push) |
| Test flaky | 1 (+2 hors liste) | `tests/typing/test_integration_nats.py::test_publisher_to_listener_e2e_discord` — `wait_for(timeout=5.0)` sous contention runner ; 3 hits le 07-01, tous en rafale dependabot | Oui (timeout 30s) |
| Conflit sémantique de merge | 1 | astryx-s9 `toast is not defined` : la branche prédatait #2123, seul le merge-ref CI pouvait le voir → **garder la CI sur refs/pull/N/merge** | Non (et c'est une feature) |

## 3. Causes racines structurelles

### 3a. Hooks locaux morts (machine-wide)
- `.git/hooks` ne contient que des `.sample` ; `pre-commit install` **refuse** (`Cowardly refusing…`) dès que `core.hooksPath` est set à n'importe quel scope — global (`~/projects/scripts/git-hooks`, hooks ccc) ET local (`.git/hooks`) le sont.
- `tools/dev-setup.sh` (set -e) **abortait donc à 100 %** à la ligne `pre-commit install`.
- Même état cassé dans **roxabi-plugins, roxabi-live, llmCLI, voiceCLI** → `tools/install-hooks.sh` y est réutilisable tel quel.
- Vérifié : des hooks écrits dans le hooks-dir commun se déclenchent aussi dans les worktrees liés ; seul bypass = `--no-verify`.

### 3b. Fan-out dependabot (67,7 % de tous les runs)
- 8 jours mesurés : **13 766 runs**, dont 9 323 sur des branches dependabot ; 11 385 (82,7 %) déclenchés par roxabi-ci[bot].
- Mécanisme : `update-behind-prs` (auto-merge.yml) met à jour **tous** les PRs ouverts à chaque push staging → le bot « édite » les PRs dependabot → dependabot refuse ensuite de les mettre à jour/fermer (**13 zombies**, le plus vieux 23 jours, #1888 vert depuis le 15-06) → chaque branche zombie a reçu **952-959 runs en 8 jours** (~137 update-branch × 7 workflows).
- Le run dependabot hebdo lui-même est sain (1 seul PR créé le 29-06) — le bruit vient du fan-out, pas de la cadence.

### 3c. Divers vérifiés
- Repo **public** → minutes Actions gratuites : les micro-optims runner-time (skip docker-build, path-filters) ne valent aucun risque. docker-build sur staging-push **seed le cache GHA cross-PR** — ne pas le supprimer.
- Branch protection staging : required = `ci` + `trufflehog` seulement — `integration` et `docker-build` sont advisory (les PRs mergent 45-61s AVANT la fin d'integration, mesuré #2121/#2122).
- `fetch-depth: 0` : 3,3s, **load-bearing** (check_debt_expiry lit les dates git) — ne pas toucher.

## 4. Implémenté dans ce lot (tout vérifié adversarialement)

| Change | Fichier | Gain vérifié |
|---|---|---|
| Commit inconditionnel `_cleanup_bare_ids` (fix racine WAL) | `src/factory/infrastructure/stores/identity/auth_store.py` | **−35s CI** (tail xdist), **−90s local**, + débloque les checkpoints prod |
| Prebundle esbuild du barrel phosphor (`deps.optimizer.web`, `enabled: true` requis) | `apps/dashboard/vitest.config.ts` | vitest 49,6s → ~24,7s (**−25s**), 117/117 pass |
| `--ignore=tests/integration --ignore=tests/e2e` sur Coverage—factory | `.github/workflows/ci.yml` | −5-10s + chaque suite exécutée exactement 1× ; couverture 83,6 → 83,0 % (floor 50) |
| integration : `needs:[ci]` supprimé + matrice inerte → 1 job | `.github/workflows/ci.yml` | −59s wall + 1 job runner ; suite jouée avec vrai NATS docker |
| `playwright install chromium --only-shell` | `.github/workflows/ci.yml` | −5,7s (le Chrome 175 MiB n'était jamais exécuté) |
| Suppression `portaudio19-dev` (vestigial : 0 dep pyaudio/sounddevice dans uv.lock) | `.github/workflows/ci.yml` | −4,4s |
| Timeouts flaky NATS 5→30s | `tests/typing/test_integration_nats.py` | tue le flake #1 (3 hits/jour) |
| `tools/install-hooks.sh` (dispatcher `pre-commit hook-impl`, contourne le refus hooksPath, re-chaîne les hooks ccc) + appel dev-setup + self-heal worktree-setup | `tools/` | hooks **actifs** main + worktrees |
| `UV_NO_SYNC=1` dans scripts/qg | `scripts/qg` | les gates en worktree ne corrompent plus uv.lock/.venv partagé |
| typecheck pre-commit → pre-push | `.claude/stack.yml` | commit ~2-4s au lieu de 12-15s ; types toujours bloqués avant CI |
| `pytest_smoke` gate pre-push (9 tests, 12s, `PYTHONPATH=src`) | `.claude/stack.yml` | premier filet pytest local (étendre le marker `smoke` au fil de l'eau) |
| `doc_drift_bundle` → pre-push | `.claude/stack.yml` | aurait évité 2/18 rouges (pattern récurrent, déjà mordu #1997) |
| Fix regex `lint_js` (`^(apps/\|…)$` ne matchait AUCUN chemin imbriqué) + extension filtre dashboard_unit_test | `.claude/stack.yml` | biome s'exécute réellement en pre-commit sur les changements frontend |
| `update-behind-prs` limité aux PRs `reviewed` + update-branch immédiat au labeling | `.github/workflows/auto-merge.yml` | fin de l'edit-poisoning → plus de zombies ; fan-out par push staging ÷ ~10 |
| Groupes dependabot : `majors` pip (les 0.x y passent), `actions` GHA, + écosystème `docker` (digests base images) | `.github/dependabot.yml` | ~15 PRs/sem → ~3-4 groupés ; path update base-images automatisé |
| `synchronize` retiré de pr-title + dependabot-automerge | workflows | −2 runs no-op par push de PR |

**Après ce lot** : job `ci` ≈ **4m50** (−75-80s), workflow complet ≈ 4m50 (integration et docker-build parallèles dedans), merge-gate = idem. Pytest local complet ≈ 10s.

## 5. Recommandé, non implémenté (ordre de valeur)

1. **Cleanup zombies (one-shot, opérateur)** — merger #1888 (CLEAN), fermer/merger les 12 autres ; sans ça le fan-out actuel persiste malgré le fix structurel. `gh pr list --author app/dependabot --state open`
2. **`gh label create dependencies`** — configuré dans dependabot.yml, n'existe pas dans le repo.
3. **Split du job ci en 3 jobs parallèles** (gates / e2e+coverage / package-coverage) → **~3m50 → ~2m45 post-lot** ; MAIS exige la mise à jour synchronisée des required checks de la branch protection (sinon deadlock auto-merge repo-wide). À faire en changement coordonné dédié.
4. **Honorer les filtres `files:` au stage CI** (QG-2, spec adversarialement corrigée en poche) : −56s sur les PRs non-frontend (53 % des PRs).
5. **Merge queue GitHub** (gratuit, repo public, compatible merge-commit) : remplace structurellement `update-behind-prs` + strict up-to-date. Prérequis : trigger `merge_group:` dans ci.yml + secret-scan.yml.
6. **Alerte staging rouge dédiée** (workflow_run CI failure sur staging → Telegram/Discord) + passer le watch GitHub en « Participating » : le rouge signifiant redevient visible (~7/jour vs 103/jour). Aujourd'hui une CI staging rouge = publish `skipped` **silencieux** (fleet sur image stale, 12,5 % des pushes staging).
7. **CD : converge « image-carried »** — chaque merge code produit **2 restarts full-fleet** (quadlet-sync sur HEAD ~7min avant que l'image existe, puis post-autoupdate). Extension du classifieur lot-4b (branche fix/lot4b, non mergée) : skiper le restart pré-image quand le diff est entièrement sous src/packages/apps. À sequencer APRÈS le merge lot-4b.
8. **Course omp-base** : publier le tag avant de merger le bump de pin (workflow_dispatch), ou retry borné dans docker-build. ~2 bumps/mois, ~10 rouges/occurrence.
9. **pytest-rerunfailures scopé** (tests/integration, tests/e2e) + `retry: 1` vitest sous CI — traite la classe flaky, pas les instances.
10. **upload-artifact `if: failure()`** (traces playwright + junit, retention 5j) — le debug de flakes se fait aujourd'hui au grep de logs bruts.
11. **enforce_admins staging** : 56 pushes staging rouges/8j = commits directs qui bypassent la protection ; chaque staging rouge fan-out du rouge sur tous les PRs ouverts.
12. Réutiliser `tools/install-hooks.sh` dans **roxabi-plugins, roxabi-live, llmCLI, voiceCLI** (même état cassé) + upstream dans dev-core (`roxabi-plugins/plugins/dev-core/tools/`).

## 6. Effets de bord à connaître

- **Hooks actifs immédiatement** pour TOUTES les sessions/worktrees de ce repo après `tools/install-hooks.sh` : commit ≈ 2-4s de gates, push ≈ 30-60s (typecheck + smoke + doc_drift + drift gates ; + vitest ~25s si frontend touché). Bypass ponctuel : `--no-verify` (¬en faire une habitude).
- `dashboard_unit_test` en pre-push exige `bun install` dans le checkout qui pushe (worktrees inclus).
- Les runs `integration (true/false)` disparaissent au profit d'un job `integration` unique — aucun n'était required check.
- doc_drift_bundle pre-push : les pushes touchant docs/symboles paieront ~5-10s et bloqueront sur du drift réel (comportement voulu).

## Sources

- Audit brut : workflow wf_ec160ece-89c (journal des 20 agents) ; run de référence 28546524122 ; fenêtre volumétrie 2026-06-24→07-01 (13 766 runs).
- Verdicts adversariaux : 12 propositions vérifiées (1 CONFIRMED, 11 ADJUSTED avec corrections intégrées ci-dessus, 2 sous-options REFUTED : fold-integration, `isolate:false` vitest — 57 tests cassés sur runner 2-core).
