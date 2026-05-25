# Post-mortem — Déploiement #1331 sur M₁

**Date :** 2026-05-25 17:25 → 18:06 (41 min)
**Issue :** #1331 TurnStore α-refactor — déploiement de `lyra-turn-writer` Quadlet en prod
**Host :** M₁ (roxabituwer, 192.168.1.16, lyra-hub role)
**Status :** Résolu, smoke test green, follow-ups identifiés

## TL;DR

Le déploiement du nouveau Quadlet `lyra-turn-writer` a déclenché 3 incidents en cascade :

1. Rotation accidentelle de **tous** les seeds NATS actifs (`lyra-acl genkeys` mode défaut)
2. Échec restart `lyra-nats` (Docker Hub rate-limit sur image au tag perdu)
3. Bug `vault_dir.mkdir()` sur ReadOnly rootfs
4. Bug ACL `_INBOX.>` (majuscule) vs `_inbox.<id>.>` (minuscule — convention NATS)

Récupération totale en 41 minutes. **Aucune perte de données** (JetStream a tenu les messages).
Downtime utilisateur visible ~30s.

## Timeline

| Time  | Event                                                                          | Severity |
| ----- | ------------------------------------------------------------------------------ | -------- |
| 17:25 | Hub déployé sur image `:staging` post-#1331 (auto-update)                      | —        |
| 17:43 | Audit prod : `lyra-turn-writer` manquant, pas de seed `turn-writer.seed`       | —        |
| 17:49 | **🔥 `uv run lyra-acl genkeys` (mode défaut) rotation de 11 seeds actifs / 13** | **HIGH** |
| 17:53 | Récupération seeds depuis Podman secrets M₁ (7/11 récupérables)                | —        |
| 17:54 | `auth.conf` regénéré (pubkeys M₁ restaurées + 4 satellites en nouveau pubkey)  | —        |
| 17:55 | `install.sh` exécuté, `lyra-nats-auth` secret rafraîchi                        | —        |
| 17:55 | `systemctl restart lyra-nats` → **❌ échec** (Docker Hub rate-limit)            | HIGH     |
| 17:57 | `lyra-hub` mort en cascade (dépendance NATS échouée)                           | MED      |
| 17:58 | Re-tag local de l'image NATS → restart OK ; flood d'auth errors `voice-client` | MED      |
| 18:01 | `lyra-hub` redémarré ; `lyra-turn-writer` start → **❌ EROFS** sur `vault_dir.mkdir()` | HIGH |
| 18:02 | Image `:staging-svc` (31h stale) re-pull pour fresh code                       | —        |
| 18:03 | Patch Quadlet : `LYRA_VAULT_DIR=/data` → start → **❌ ACL** `_INBOX.>` vs `_inbox.turn-writer.>` | MED |
| 18:05 | Patch `acl-matrix.json` lowercase → regen → restart NATS → ✅ turn-writer connecté | —     |
| 18:06 | Auth-flood `voice-client` subside spontanément ; smoke test green              | —        |

## Causes racines

### 1. Rotation accidentelle des seeds (la pire)

**Cause :** `lyra-acl genkeys` (mode défaut = `_mode_full_provision`) régénère **inconditionnellement** tous les seeds actifs (`scripts/_modes.py:290-297`).
Pas de mode "add-only" pour intégrer un nouvel identifiant.

```python
for name in active:
    seed = provider.gen_seed(name)   # ← écrase TOUT
    atomic_write(seed_file, seed_str, 0o600)
```

**Trigger :** Besoin de générer un nouveau seed `turn-writer.seed`. Seul le mode `--regen-authconf` ne touche pas aux seeds, mais il **exige** que tous les seeds existent déjà — paradoxe pour un nouvel identifiant.

**Évité de justesse :** Les Podman secrets stockent les seeds en clair, récupérables via `podman secret inspect --showsecret`. **7/11 seeds restaurés** depuis le store M₁.
Les 4 satellites (`monitor`, `image-worker`, `llm-operator`, `voice-client`) auraient eu leurs secrets sur M₂ → non récupérés ici. Leur identité reste rotée. Heureusement dormants.

### 2. Restart NATS échoué (Docker Hub rate-limit)

**Cause :** L'image `docker.io/library/nats:2.10.29-alpine` était locale mais avec `RepoTags=<none>` (probablement après un auto-update ou cleanup). `podman generate systemd` cherchait l'image par tag → fallback pull → rate-limit unauthenticated.

**Détection :** Logs `lyra-nats`:
```
toomanyrequests: You have reached your unauthenticated pull rate limit
```

**Fix :** `podman tag <image-id> docker.io/library/nats:2.10.29-alpine` — l'image était là, juste sans tag.

### 3. `vault_dir.mkdir()` sur ReadOnly rootfs

**Cause :** Bug dans `src/lyra/bootstrap/standalone/turn_writer_standalone.py:46` :

```python
vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
vault_dir.mkdir(parents=True, exist_ok=True)              # ← inconditionnel
db_path = Path(os.environ.get("LYRA_TURNS_DB") or (vault_dir / "turns.db"))
```

Avec `ReadOnly=true` + `LYRA_TURNS_DB=/data/turns.db` mais sans `LYRA_VAULT_DIR`, `vault_dir` résout à `/home/lyra/.lyra` (rootfs read-only) → EROFS.

**Couverture test absente :** Les tests `test_writer.py` mockent le store, n'exercent jamais le bootstrap réel sous ReadOnly. La revue de PR a aligné B5 (LYRA_TURNS_DB respecté) mais a manqué que la ligne 46 doit aussi être conditionnelle.

**Workaround live :** `Environment=LYRA_VAULT_DIR=/data` dans le Quadlet.

### 4. Bug ACL `_INBOX.>` vs `_inbox.turn-writer.>`

**Cause :** `deploy/nats/acl-matrix.json` pour turn-writer utilise majuscules `_INBOX.>`, mais `roxabi_nats.connect()` avec `identity_name="turn-writer"` génère des inboxes minuscules `_inbox.turn-writer.<id>.*`. **NATS subjects sont case-sensitive.**

**Convention violée :** Toutes les autres identités utilisent `_inbox.<id>.>` (12 entrées dans la matrix). Seul turn-writer dévie.

**Couverture test absente :** Mêmes tests mockent NATS. L'intégration `test_e2e_telegram_to_agent.py` n'exerce pas turn-writer en mode pull-subscribe contre ACL réelle.

### 5. Cascade `lyra-hub` (effet secondaire)

`Requires=lyra-nats.service` + échec start NATS → systemd marque hub comme `Dependency failed` définitivement, pas de retry auto même après NATS rétabli. **Pattern systemd standard**, pas un bug ; juste à connaître pour les ops manuels.

## Ce qui a marché

- **Podman secret store comme backup de fait** des seeds → récupération sans Syncthing/git versioning
- **`auth.conf.bak.*` rotatifs** générés par `--regen-authconf` → historique des pubkeys (pas des seeds)
- **`AUTH_DIR=$HOME/...` env var** dans `_require_root()` → bypass root pour container-only NATS (M₁ pattern)
- **JetStream queueing** → aucun turn perdu pendant la fenêtre d'instabilité NATS
- **Health endpoint + Prometheus gauge** → diagnostic immédiat post-recovery
- **WAL sidecars via directory bind-mount** (fix B4) → fonctionne en prod, EROFS évité côté DB

## Actions de suivi

| #   | Action                                                                                                                                                                                                 | Priorité      | Statut |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- | ------ |
| 1   | **PR source** : `lyra-acl genkeys --add-identity <name>` pour générer UN seul seed sans toucher aux autres                                                                                              | **HIGH**      | open — tracked in follow-up issue |
| 2   | **PR source** : guard `vault_dir.mkdir()` si `LYRA_TURNS_DB` set → skip mkdir, OR try/except OSError                                                                                                    | **HIGH**      | ✅ #1359 / PR #1360 (mkdir → db_path.parent) |
| 3   | **PR source** : fix `acl-matrix.json` turn-writer `_INBOX.>` → `_inbox.turn-writer.>` (déjà fait live sur M₁)                                                                                           | **HIGH**      | ✅ #1359 / PR #1360 |
| 4   | **PR source** : `deploy/quadlet/lyra-turn-writer.container` ajouter `Environment=LYRA_VAULT_DIR=/data`                                                                                                  | **HIGH**      | ✅ #1359 / PR #1360 |
| 5   | **CI** : test intégration avec auth.conf réelle ET ReadOnly rootfs (catch #2, #3, #4 d'un coup)                                                                                                         | MED           | open — tracked in follow-up issue |
| 6   | **Runbook** : `make nats-add-identity` qui chaîne `genkeys --add-identity` + secret create + restart sans re-rotation                                                                                   | MED           | open — tracked in follow-up issue |
| 7   | **Runbook** : pre-restart vérifier `podman images \| grep -v '<none>'` pour les images critiques (catch #2)                                                                                             | LOW           | open — tracked in follow-up issue |
| 8   | **Refresh M₂** : satellites (`monitor`, `image-worker`, `llm-operator`) ont pubkey neuf en `auth.conf`. Quand un service revient en ligne, refresh son Podman secret depuis le seed Syncthing-syncé    | LOW (dormants) | open — tracked in follow-up issue |
| 9   | **Doc** : ajouter à `docs/ops/nats-identity-lifecycle.md` un cas "ajouter une nouvelle identité"                                                                                                        | LOW           | open — tracked in follow-up issue |

**Source-tree fixes** (actions 2-4) shipped in PR #1360 / issue #1359 (parent #1277, blocked-by #1331). Verified live on M₁ pre-PR.

**Structural follow-ups** (actions 1, 5–9) tracked in a separate issue (see related), blocked-by #1331 for lineage.

## Métrique de l'incident

- **Durée totale :** 41 min (17:25 audit → 18:06 green)
- **Downtime utilisateur visible :** ~30 s (restart NATS + cascade hub) — Telegram/Discord adapters ont reconnecté
- **Données perdues :** 0 (JetStream queueing)
- **Identités cassées de façon permanente sur M₂ :** 4 (satellites dormants, à refresh à la prochaine activation)

## Préventions — si je le refaisais

1. **Avant `genkeys` :** `cp -a ~/.lyra/nkeys ~/.lyra/nkeys.bak.$(date +%s)` — backup local
2. **Avant restart d'un service Quadlet sensible :** `podman images | grep <image>` → si `<none>` ou absent, `podman pull` d'abord, en dehors de la fenêtre de restart
3. **Pour un nouvel identifiant** : éviter `genkeys` mode défaut. Soit modifier `_modes.py` pour supporter un mode partiel, soit générer manuellement la seed (`scripts/nkeys_nats.py` ou équivalent) + `--regen-authconf`
4. **Test fumée local** avant prod : `make test-integration` aurait dû catch #3+#4 si la suite inclut un container ReadOnly avec auth.conf réelle

## État final (smoke test green)

| Host | Component                                              | Status   |
| ---- | ------------------------------------------------------ | -------- |
| M₁   | lyra-{nats,hub,telegram,discord,clipool,turn-writer}   | ✅ active |
| M₁   | voicecli-{stt,tts}                                     | ✅ active |
| M₂   | llmcli, llmcli-nats-worker                             | ✅ active |
| —    | Stream `LYRA_TURNS` + consumer `turn-writer-v1`        | ✅ ready  |
| —    | `~/.lyra/turn-writer/turns.db` (schema + WAL)          | ✅ OK     |
| —    | M₁ NATS auth flood                                     | ✅ subsided |
