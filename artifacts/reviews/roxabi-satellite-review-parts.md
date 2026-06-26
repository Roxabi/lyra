# roxabi-satellite — plan de review découpé

> À utiliser **avant** `/dev-core:code-review` — une passe par partie, dans l’ordre.
> Chaque partie cible un périmètre autonome ; les dépendances entre PRs sont notées.

## PRs

| # | Repo | Branche | Base | Dépend de |
|---|------|---------|------|-----------|
| 1 | roxabi-factory | `feat/roxabi-satellite` | `staging` | — |
| 2 | voiceCLI | `feat/roxabi-satellite` | `staging` | PR#1 mergée (ou pin git staging) |
| 3 | imageCLI | `feat/roxabi-satellite` | `staging` | PR#1 |
| 4 | llmCLI | `feat/roxabi-satellite` | `staging` | PR#1 |

## Parties de review (factory — PR 1)

### Partie A — Package `roxabi-satellite` (fondation)

**Fichiers:**
- `packages/roxabi-satellite/src/roxabi_satellite/{blobs,envelope,tokens,errors,__init__}.py`
- `packages/roxabi-satellite/tests/test_blobs.py`
- `packages/roxabi-satellite/pyproject.toml`, `README.md`, `AGENTS.md`

**Focus:** singleton blobstore ADR-068, alias `FACTORY_BLOBSTORE_*`, API publique, pas d’import `roxabi_nats._*`.

**Commande:** `/dev-core:code-review` sur PR factory — filtrer mentally ou via diff scope Partie A.

---

### Partie B — Domaines `voice` + `socialmedia`

**Fichiers:**
- `packages/roxabi-satellite/src/roxabi_satellite/voice/**`
- `packages/roxabi-satellite/src/roxabi_satellite/socialmedia/**`
- `packages/roxabi-satellite/tests/test_voice_*.py`, `test_socialmedia.py`

**Focus:** validation ingress, builders d’erreur wire-safe, mapping Postiz → `WorkerError`, cohérence avec `roxabi_contracts`.

---

### Partie C — Domaines `image` + `llm`

**Fichiers:**
- `packages/roxabi-satellite/src/roxabi_satellite/image/**`
- `packages/roxabi-satellite/src/roxabi_satellite/llm/**`
- `packages/roxabi-satellite/tests/test_image.py`, `test_llm.py`

**Focus:** legacy error codes image, sanitization httpx, réponses LLM stream/blocking.

---

### Partie D — Intégration Factory (hub + Posties)

**Fichiers:**
- `src/factory/adapters/socialmedia/{adapter,daemon,media}.py`
- `src/factory/nats/{nats_bus,worker_registry}.py`
- `src/factory/adapters/nats/nats_outbound_listener.py`
- `pyproject.toml`, `uv.lock`

**Focus:** queue group `SUBJECTS.workers`, migration tokens hub, daemon blobstore, pas de régression Postiz client.

---

## Parties de review (satellites CLI)

### Partie E — voiceCLI

**Fichiers:** `src/voicecli/adapters/nats/*`, `pyproject.toml`, `tests/nats/*`, golden STT.

**Focus:** re-exports, adapters allégés, tests blobs patch `satellite_blobs._INSTANCE`.

---

### Partie F — imageCLI (scope migration uniquement)

**Fichiers:** `pyproject.toml`, `src/imagecli/nats/*`, `src/imagecli/commands/serve.py` (`_init_blob_store`), `tests/nats/test_adapter.py`.

**Focus:** `build_image_error_reply`, pas de fuite httpx dans les logs.

**Hors scope PR:** scripts enishu, `engine/base.py` metadata — ne pas inclure dans la review.

---

### Partie G — llmCLI

**Fichiers:** `src/llmcli/nats/_generation.py`, `pyproject.toml`, `tests/nats/*`.

**Focus:** délégation `build_llm_error_reply`, chemins stream vs blocking inchangés.

---

## Ordre recommandé

1. Factory A → B → C → D (ou une PR, 4 passes review)
2. Après merge Factory (ou en parallèle avec pin path): E, F, G en parallèle

## Matrice agents `/dev-core:code-review`

| Partie | security | architect | backend-dev | tester | devops |
|--------|:--------:|:---------:|:-----------:|:------:|:------:|
| A | ✓ | ✓ | ✓ | ✓ | — |
| B | ✓ | ✓ | ✓ | ✓ | — |
| C | ✓ | — | ✓ | ✓ | — |
| D | ✓ | ✓ | ✓ | ✓ | ✓ (quadlet indirect) |
| E–G | ✓ | — | ✓ | ✓ | — |

## Secret scan

Avant chaque passe: aucun token réel dans diff ; `path = ../roxabi-factory` acceptable en dev, documenter passage `git` subdirectory post-merge.