# Lyra Engine — Architecture Overview

> ⚠️ **Largely stale** — This document was written early in the project and predates the Phase 1b architecture refactoring (2026-03-16/17). Key differences: old class names (`Message` → `InboundMessage`, `Bus` → `InboundBus`/`OutboundDispatcher`, `AuthMiddleware` → `Authenticator` + `GuardChain`), old memory levels (§10 uses early L1–L5 scheme vs current 5-level model), old module layout (monolithic files decomposed into ≤300 LOC modules), `EventBus` removed. For the current architecture, see [ARCHITECTURE.md](../ARCHITECTURE.md).
>
> Recap complet de l'architecture cible. Dernière mise à jour : 2026-03-12.
> Diagramme interactif : `docs/architecture-visual-explainer.html`
> Source Excalidraw : `docs/architecture-visual.excalidraw`

---

## Vue d'ensemble

Lyra est un moteur IA **hub-and-spoke asyncio Python**. Les messages entrent par des **Adapters** de canaux, transitent par un **Bus central**, sont routés vers des **Agent Pools**, qui appellent des **LLM Wrappers** ou **CLI Wrappers**, avec lecture/écriture dans un **système de mémoire 5 niveaux**.

```
[Channel Adapters] → [Bus + Router] → [Agent Pools] → [LLM / CLI Wrappers]
                                            ↕
                                     [Memory System]
```

---

## 1. Channel Adapters

**Rôle :** Traduire les événements canal ↔ domaine Lyra (pattern GOF Adapter).

| Adapter | Protocole | Statut |
|---------|-----------|--------|
| `TelegramAdapter` | polling / webhook | actif |
| `DiscordAdapter` | websocket | prévu |
| `CLIAdapter` | stdin / stdout | actif |
| `HTTPAdapter` | FastAPI endpoint | futur |

Chaque adapter expose :
- `inbound_queue: asyncio.Queue[Message]` → écrit vers le Bus
- `outbound_queue: asyncio.Queue[Response]` → lit depuis le Bus
- `normalize(raw_event) → Message` — traduction entrante
- `format(Response) → channel output` — traduction sortante (Markdown, clavier inline, voix, fichier)

> **Adapter ≠ Wrapper** : un Adapter traduit un domaine externe. Un Wrapper encapsule un outil interne avec une interface uniforme.

---

## 2. Schéma Message

```python
class Message:
    id: UUID
    channel: str          # "telegram" | "discord" | "cli"
    user_id: str
    session_id: str       # clé de continuité
    content: str
    attachments: list[Attachment]
    metadata: dict        # données canal (chat_id, message_id…)
    timestamp: datetime
    reply_to: UUID | None
    tool_result: ToolResult | None  # rempli lors du tool forwarding
    trust_level: TrustLevel         # OWNER | TRUSTED | PUBLIC | BLOCKED
```

---

## 3. Bus Central

**Rôle :** Orchestrer le routage — ce n'est PAS un simple relay.

```python
class Bus:
    inbound: asyncio.Queue[Message]
    outbound: asyncio.Queue[Response]
    router: Router
```

Le Bus lit l'`inbound`, délègue au Router pour obtenir le `pool_id`, dispatch vers l'Agent Pool correspondant, puis écrit la `Response` dans l'`outbound`.

---

## 4. Router

**Rôle :** Associer chaque message au bon agent pool.

```python
class Router:
    bindings: dict[tuple[str, str], tuple[str, str]]
    # (channel, user_id) → (agent_id, pool_id)
```

Logique :
1. Lookup par `(channel, user_id)` — binding explicite
2. Lookup par `session_id` — continuité de session
3. Fallback sur le binding par défaut du canal

---

## 5. Agent Pools

Chaque pool est isolé par un `asyncio.Lock` — pas de réponses concurrentes dans une même session.

### AgentIdentity

```python
class AgentIdentity:
    agent_id: str
    name: str           # "Lyra", "Analyst"…
    personality: str
    system_prompt: str
    tools: list[Tool]
    memory_scope: MemoryScope   # quels niveaux mémoire sont accessibles
    llm_config: LLMConfig
    skills: list[str]           # chemins de skills disponibles
```

### Pool management

- `asyncio.Lock` par `pool_id` → une seule exécution à la fois par session
- Plusieurs pools tournent **en parallèle** pour des users/sessions différents
- Sub-agents possibles via pattern **ReAct**

---

## 6. Schéma Response

```python
class Response:
    id: UUID
    request_id: UUID       # lien vers le Message source
    session_id: str
    channel: str
    user_id: str
    content: str
    response_type: str     # "text" | "tool_forward" | "voice" | "file" | "error"
    attachments: list[Attachment]
    tool_forward: ToolForwardRequest | None
    routing: RoutingContext
    metadata: dict
```

### RoutingContext

```python
class RoutingContext:
    channel: str
    bot_id: str            # quel bot répond (multi-bot)
    chat_id: str           # chat / guild / channel cible
    thread_id: str | None  # forum thread, Discord thread
    reply_to_message_id: str | None
    user_id: str
    session_id: str
```

L'Adapter sortant vérifie `response.routing.channel == self.channel` et `bot_id == self.bot_id` avant d'envoyer.

---

## 7. Tool Forwarding — AskUserQuestion pattern

Quand un agent appelle un outil nécessitant une réponse utilisateur (`AskUserQuestion`, `ConfirmAction`…) :

```
Agent → ToolForwardMessage → Bus outbound (type: tool_forward)
     → Adapter format (clavier inline / prompt CLI)
     → User répond
     → Message(tool_result: ToolResult) → Bus inbound
     → Bus route vers l'agent (même session_id)
     → Agent reprend l'exécution
```

**Invariant clé :** ce n'est PAS une nouvelle session. Même `session_id`, même pool, `asyncio.Lock` maintenu pendant l'attente. L'agent est suspendu, pas terminé.

---

## 8. LLM Backends (Wrappers)

```python
class LLMConfig:
    provider: str     # "anthropic" | "ollama" | "openai"
    model: str
    endpoint: str | None  # pour Ollama sur Machine 2
    temperature: float
    max_tokens: int
    streaming: bool
```

| Wrapper | Cible | Rôle |
|---------|-------|------|
| `AnthropicWrapper` | Anthropic API | Défaut (cloud) |
| `OllamaWrapper` | Machine 2 FastAPI `/llm` | Fallback / offline |
| `OpenAIWrapper` | OpenAI API | Futur |

---

## 9. CLI Wrappers

Pour les outils externes appelés en subprocess :

| Wrapper | Commande | Note |
|---------|----------|------|
| `VoiceCLIWrapper` | `voicecli --chunked` | ⚠ CWD = `~/projects/voiceCLI/` obligatoire |
| `EmbeddingCLIWrapper` | TBD | Futur |

Chaque wrapper gère : subprocess, CWD, env vars, parsing stdout/stderr.

---

## 10. Système de mémoire — 5 niveaux

> ⚠️ **Stale** — schéma de niveaux révisé. Voir [ARCHITECTURE.md → Memory Layer](../ARCHITECTURE.md#memory-layer-5-levels) pour le modèle courant.

| Niveau | Nom | Stockage | TTL | Scope | Statut |
|--------|-----|----------|-----|-------|--------|
| L0 | Working Memory | `pool.sdk_history` (in-process list) | Session | Par pool | ✅ Shipped (#83) |
| L1 | Session Memory | asyncio store (keyed by `session_id`) | Session | Par pool | Phase 2 |
| L2 | Episodic Memory | Markdown daté | Permanent | Par user | Phase 2 |
| L3 | Semantic Memory | SQLite + FTS5/BM25 + sqlite-vec (roxabi-vault) | Permanent | Par namespace | ✅ Shipped (#78/#81/#82/#83) |
| L4 | Procedural Memory | SQLite, extracted via LLM | Permanent | Par user | Phase 3 |

**MemoryManager (src/lyra/core/memory.py, #83) :**
- `recall(user_id, namespace, first_msg, token_budget)` → blocs `[MEMORY]` + `[PREFERENCES]` injectés dans le system prompt
- `upsert_session(snap, summary)` → flush L3 en fin de session ou compaction
- `upsert_concept()` / `upsert_preference()` → extraction background post-session
- Compaction L0 : `compact()` déclenché à 80% de 200k tokens → résumé LLM + tail 10 turns

---

## Flux complet (happy path)

```
User (Telegram)
  → TelegramAdapter.normalize() → Message
  → Bus.inbound.put(msg)
  → Router.dispatch() → pool_id
  → AgentPool.acquire_lock()
  → MemoryManager.recall(user_id, namespace, first_msg) → [MEMORY]/[PREFERENCES] context
  → ComplexityEstimator → LLMConfig
  → AnthropicWrapper.complete(system_prompt + context) → text
  → (on eviction) MemoryManager.upsert_session() + _schedule_extraction()
  → Response(type="text", routing=RoutingContext)
  → Bus.outbound.put(response)
  → TelegramAdapter.format() → sendMessage
  → AgentPool.release_lock()
```
