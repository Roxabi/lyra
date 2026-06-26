# 03 — Audit mutualisation (A2)

## Verdict global

Le niveau de mutualisation est **bon sur les adapters platform** (streaming délégué à
`adapters/shared/` via `PlatformCallbacks`, `send_with_retry`, `SqliteStore` base class) mais
**moyen sur `src/lyra/nats/`** : le pattern heartbeat/subscribe/unsubscribe est copié
verbatim dans les 4 clients NATS sans base class. Le premier levier est l'extraction d'un
`NatsWorkerClientMixin` dans `roxabi-nats`. Le second levier est la rationalisation du
pattern `_DEFAULT_NATS_URL` + résolution env dans les CLI tools (2 occurrences identiques).

---

## Top 10 findings

| # | Titre | Sites concernés | Type | Effort | Impact | Recommandation |
|---|-------|-----------------|------|--------|--------|----------------|
| 1 | Heartbeat lifecycle répété ×4 | `nats/nats_llm_client.py:112`, `nats/nats_tts_client.py:106`, `nats/nats_stt_client.py:118`, `nats/nats_image_client.py:100` | near-clone | M | fort | Extraire `NatsWorkerClientBase` dans `roxabi-nats` |
| 2 | `_on_heartbeat` body copié ×4 | `nats/nats_tts_client.py:124`, `nats/nats_stt_client.py:136`, `nats/nats_image_client.py:114`, `nats/nats_llm_client.py:132` | near-clone | M | fort | Factoriser dans la base class (finding 1) |
| 3 | `_DEFAULT_NATS_URL` + env resolution ×2 | `cli_ops.py:29,236`, `cli_voice_smoke.py:30,75` | pattern-répété | S | moyen | Extraire `_resolve_nats_url()` dans `cli.py` ou module utils |
| 4 | `NatsLlmClient` mélange transport et logique applicative | `nats/nats_llm_client.py:155-192` (text-folding rule, `LlmProvider` protocol) | extraction-package | L | fort | Garder in-tree — logique de contrat LLM est applicative; ¬extraire dans roxabi-nats |
| 5 | `_render_reasoning` quasi-identique Telegram/Discord | `telegram_outbound.py:313`, `discord_outbound.py` (pattern identical) | near-clone | S | moyen | Factoriser dans `adapters/shared/_shared_streaming_emitter.py` |
| 6 | `stores/` sans protocol ×5 | `infra/stores/auth_store.py:40`, `bot_agent_map.py:19`, `credential_store.py:94`, `message_index.py:34`, `prefs_store.py:43` | pattern-répété | M | moyen | Ajouter protocols ADR-048 manquants (déjà planifié dans la dette) |
| 7 | `codec` streaming vs `cli_streaming_parser` | `nats/render_event_codec.py:1`, `core/cli/cli_streaming_parser.py:1` | — | — | faible | Complémentaires — fausse piste (voir §Fausses pistes) |
| 8 | `llm/errors.py` = shim pur | `llm/errors.py:1-6` | pattern-répété | S | faible | Supprimer le fichier shim, rediriger imports vers canonical path |
| 9 | `send_with_retry` in `adapters/shared` vs retry in `_dispatch.py` | `adapters/shared/_shared.py:219`, `core/hub/outbound/_dispatch.py:105` | near-clone | S | moyen | Invariants divergent — fausse piste (voir §Fausses pistes) |
| 10 | `_parse_*_timeout()` copié ×3 | `nats/nats_llm_client.py:59`, `nats/nats_stt_client.py` (impl similaire), `nats/nats_tts_client.py` (impl similaire) | near-clone | S | faible | Consolider dans module `nats/_timeout.py` in-tree |

---

## Détail des Top 10

### 1. Heartbeat lifecycle répété ×4

- **Sites**: `nats/nats_llm_client.py:112-121`, `nats/nats_tts_client.py:106-118`, `nats/nats_stt_client.py:118-130`, `nats/nats_image_client.py:100-112`
- **Constat** (lecture manuelle): les 4 classes ont exactement la même structure `start()`/`stop()` : guard `if self._hb_sub is None`, `nc.subscribe(SUBJECTS.xxx_heartbeat, cb=self._on_heartbeat)`, unsubscribe + set-None. Seule la constante de sujet diffère.
- **Pourquoi mutualisable**: invariant commun — "un client NATS worker possède exactement 1 subscription heartbeat, idempotente au start/stop". Aucune divergence de politique d'erreur ou de timeout entre les 4.
- **Cible proposée**: `packages/roxabi-nats/` — `NatsWorkerClientBase` avec `start(subject)` / `stop()` + abstract `_on_heartbeat_data(data: dict) -> None`.
- **Recommandation**: Extraire la base class dans `roxabi-nats` (côte à côte avec `NatsDriverBase` existant). Les 4 clients in-tree héritent + injectent le sujet via `__init__`.

### 2. `_on_heartbeat` body copié ×4

- **Sites**: `nats/nats_tts_client.py:124-152`, `nats/nats_stt_client.py:136-163`, `nats/nats_image_client.py:114-142`, `nats/nats_llm_client.py:132-149`
- **Constat** (lecture manuelle): le corps est identique — parse JSON, vérif `worker_id` str, appel `validate_worker_id()`, `self._registry.record_heartbeat(data)`. Seuls les log prefixes diffèrent (`stt_client:`, `tts_client:`, etc.).
- **Pourquoi mutualisable**: invariants identiques — même politique de rejet d'un `worker_id` invalide, même appel `record_heartbeat`. La divergence est cosmétique (log prefix).
- **Cible proposée**: Couvre par le finding 1 — la base class `NatsWorkerClientBase` implémente le dispatch complet, les subclasses fournissent le log prefix via attribut de classe.
- **Recommandation**: Résoudre en même temps que finding 1.

### 3. `_DEFAULT_NATS_URL` + résolution env ×2

- **Sites**: `cli_ops.py:29,236`, `cli_voice_smoke.py:30,75`
- **Constat** (lecture manuelle): `_DEFAULT_NATS_URL = "nats://localhost:4222"` défini aux deux endroits. Pattern `nats_url or os.environ.get("NATS_URL", _DEFAULT_NATS_URL)` répété à `cli_ops.py:236` et `cli_voice_smoke.py:75`.
- **Pourquoi mutualisable**: invariant identique — fallback `NATS_URL` env puis constante. `cli_setup.py` et `cli_agent.py` n'ont pas de NATS URL donc le scope est limité.
- **Cible proposée**: `src/lyra/cli.py` — fonction module-level `_resolve_nats_url(opt: str | None) -> str` réutilisée par `cli_ops` et `cli_voice_smoke`.
- **Recommandation**: S mineur, extractable sans ADR. Réduire les risques de divergence future si une 3e commande NATS-aware est ajoutée.

### 4. NatsLlmClient — mécanique transport vs logique applicative LLM

- **Sites**: `nats/nats_llm_client.py:155-192` (text-folding rule), `nats/nats_llm_client.py:85-239` (classe entière, 628 LOC)
- **Constat** (lecture manuelle + CLAUDE.md nats): le fichier documente explicitement "NatsLlmClient lives here, not in llm/ because it depends on NATS internals". La classe implémente `LlmProvider` protocol (logique applicative) et gère `WorkerRegistry` + `NatsCircuitBreaker` (transport). La règle de text-folding (`_build_request`) est un invariant de contrat LLM, pas de transport pur.
- **Pourquoi mutualisable**: aucun — la logique de contrat LLM (`LlmRequest`, `LlmResponse`, text-folding) est spécifique à Lyra. `roxabi-nats` est domain-agnostic par définition (ADR-047 Rule 1: "packages/roxabi-nats/ has zero lyra.* subject knowledge").
- **Cible proposée**: rester in-tree. Le finding 1 (base class heartbeat) est le seul candidat légitime pour extraction.
- **Recommandation**: ¬extraire NatsLlmClient. Extraire uniquement le boilerplate start/stop (finding 1).

### 5. `_render_reasoning` quasi-identique Telegram/Discord

- **Sites**: `adapters/telegram/telegram_outbound.py:313-361`, `adapters/discord/discord_outbound.py` (structure identique non lue en détail)
- **Constat** (lecture telegram): logique d'accumulation text + throttle `STREAMING_EDIT_INTERVAL` + troncation 120 chars + `_dim_italic()` wrapping. La mécanique d'accumulation/throttle est identique sur les deux platforms.
- **Pourquoi mutualisable**: le throttle state (`_reasoning_accum_cell`, `_last_reasoning_edit_cell`) et la troncation à 120 chars sont des invariants partagés. La divergence est uniquement dans l'appel d'édition platform-specific (edit_message_text vs discord channel.edit).
- **Cible proposée**: `adapters/shared/_shared_streaming_emitter.py` — extraire `_build_reasoning_accumulator()` qui retourne un closure capturant l'état et appelant un callback platform-injectable.
- **Recommandation**: S modéré. Déjà dans la cible naturelle (`_shared_streaming_emitter.py` possède `PlatformCallbacks`). Réduire les bugs de throttle asymétrique entre platforms.

### 6. Stores sans protocol — ratio 4:10 au lieu de 1:1

- **Sites**: `infrastructure/stores/auth_store.py:40`, `bot_agent_map.py:19`, `credential_store.py:94`, `message_index.py:34`, `prefs_store.py:43`
- **Constat** (lecture liste fichiers): `core/stores/` a 4 protocols formels (`AgentStoreProtocol`, `ThreadStoreProtocol`, `IdentityAliasStoreProtocol`, `PairingManagerProtocol`). Les 10 stores SQLite dans `infra/stores/` incluent 5 sans protocol correspondant dans `core/stores/`.
- **Pourquoi mutualisable**: l'ADR-048 prescrit "Protocols → core/stores | Implementations → infrastructure/stores". Les 5 stores orphelins de protocol ne peuvent pas être mockés pour les tests unitaires sans importer `infrastructure`.
- **Cible proposée**: `src/lyra/core/stores/` — ajouter les 5 protocols manquants. Déjà dans la dette `DEBT:importlinter-adr048-transition`.
- **Recommandation**: Tracked comme dette ouverte. Ne pas ré-analyser ici, confirmer que les 5 manquants sont bien ciblés dans la migration ADR-048.

### 7. (Fausse piste — voir §Fausses pistes)

### 8. `llm/errors.py` = shim pur

- **Sites**: `src/lyra/llm/errors.py:1-6`
- **Constat** (lecture directe): 6 lignes — import unique `LlmUnavailableError` depuis `lyra.core.ports.llm` + re-export. CLAUDE.md llm documente "Shim — re-exports LlmUnavailableError from lyra.core.ports.llm. Useful for import resolution; prefer the canonical path in new code."
- **Pourquoi mutualisable**: le fichier est explicitement un shim backward-compat. Sa seule valeur est la compatibilité import. Aucune duplication fonctionnelle.
- **Cible proposée**: décision de suppression ou maintien — A3 simplification. Mentionné ici car le shim est la conséquence d'une duplication passée.
- **Recommandation**: À transmettre à A3 (simplification/dead code). Ce finding n'est pas de la duplication active.

### 9. (Fausse piste — voir §Fausses pistes)

### 10. `_parse_*_timeout()` copié ×3

- **Sites**: `nats/nats_llm_client.py:59-82`, `nats/nats_stt_client.py` (pattern similaire), `nats/nats_tts_client.py` (pattern similaire)
- **Constat** (lecture nats_llm_client): `_parse_llm_timeout()` applique clamp `[5.0, 600.0]` + fallback env `LYRA_LLM_TIMEOUT`. Patterns STT/TTS ont probablement leur propre env var + range.
- **Pourquoi mutualisable**: squelette identique (parse float, clamp range, warn + fallback). Divergence intentionnelle sur les valeurs de range et noms env var.
- **Cible proposée**: `src/lyra/nats/_timeout.py` — `parse_nats_timeout(env_var, min, max, default)` générique. In-tree, pas extraction package (les valeurs de range sont applicatives).
- **Recommandation**: S faible. Consolider pour éviter une divergence future sur la politique de warning.

---

## Findings additionnels

- `adapters/discord/discord_outbound.py:74` : retry exponential backoff inline `asyncio.sleep(1.0 * (2**_attempt))` dans `_discord_typing_worker` — même formule que `send_with_retry` dans `_shared.py`, mais scopes différents (typing vs send). À transmettre A3 si typing worker est simplifié.
- `src/lyra/config.py:33` : `load_config = load_telegram_config` — alias backward-compat similaire à llm/errors.py shim. À transmettre A3.
- `cli_ops.py` et `cli_voice_smoke.py` : tous deux `from roxabi_nats.connect import nats_connect` ou `_build_tls_context` direct — pas de wrapper partagé pour la connexion CLI NATS. Lié finding 3.
- Les 4 `_parse_*_timeout()` utilisent le même pattern `log.warning` avec f-string identique — candidat trivial pour factorisation in-tree (finding 10).
- `worker_registry.py` (non lu en détail) est partagé entre les 4 clients via import direct — c'est correct, pas de duplication.
- `PlatformCallbacks.edit_trace` documenté comme vestigial `# DEBT: vestigial — no live consumer post-#1214` — à transmettre A3.

---

## Fausses pistes écartées

**`nats/render_event_codec.py` vs `core/cli/cli_streaming_parser.py`** : les deux manipulent du streaming mais les invariants sont orthogonaux. `render_event_codec.py` encode/décode des `RenderEvent` (format hub↔adapter NATS, registry-driven, type dispatch). `cli_streaming_parser.py` parse des NDJSON CLI subprocess (Anthropic wire format, stateful, orienté LlmEvent). Les types d'événements, les formats de payload et les consommateurs sont complètement différents. Toute tentative de factorisation briserait les invariants versioning schema de l'un ou l'autre.

**`send_with_retry` (`adapters/shared/_shared.py:219`) vs retry dans `_dispatch.py` (`core/hub/outbound/_dispatch.py:105`)** : les deux font un backoff exponentiel `(1, 2, 4)` mais les invariants divergent profondément. `send_with_retry` est cosmétique (swallows exceptions, returns normally après exhaustion — "silent skip acceptable") ; `_dispatch.py` retry est opérationnel (conditionnel sur `_is_transient_error`, circuit breaker recording, `kind == "send"` only, callback post-send). Factoriser serait un bug : la sémantique "échec silencieux" de `send_with_retry` ne peut pas s'appliquer à la livraison principale.

**`core/stores/` (4 protocols) vs `infrastructure/stores/` (10 impls)** : le ratio asymétrique n'est pas une duplication mais une migration en cours (DEBT:importlinter-adr048-transition). Les 5 stores sans protocol sont des candidats à complétion, pas à réduction. Déjà tracké.

**`cli_agent_create.py` vs `cli_agent.py`** : les deux ont des imports Typer mais leurs domaines sont distincts (create interactive wizard vs list/edit/validate). L'organisation actuelle est correcte — pas de duplication de setup.

---

## Hors scope / non vérifié

- `adapters/discord/discord_outbound.py` : lu seulement les 100 premières lignes. Le `build_streaming_callbacks` discord (analogue de `telegram_outbound.py:build_streaming_callbacks`) n'a pas été lu en détail — finding 5 repose sur l'inference de structure par isomorphisme d'imports.
- `nats/nats_stt_client.py` et `nats/nats_tts_client.py` : `_parse_stt_timeout()` et `_parse_tts_timeout()` non lus — finding 10 basé sur le pattern llm_client + inférence structurelle.
- `packages/roxabi-nats/src/roxabi_nats/` : `driver_base.py` non lu — potentiel overlap avec finding 1 si une base class de heartbeat y existe déjà.
- `bootstrap/factory/wiring_helpers.py` (410 LOC) : non lu — out of scope A2, relève de A3 (simplification God module).
- `src/lyra/monitoring/` (961 LOC) : non audité.
- `ccc` (cocoindex) non utilisé — index non vérifié, fallback grep.
