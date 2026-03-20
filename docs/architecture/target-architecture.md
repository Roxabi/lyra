# Target Architecture — Hexagonal / Ports & Adapters

> Status: CIBLE — pas encore implémentée
> Date: 2026-03-19
> Contexte: Discussion UX tool_use Telegram → généralisation à l'archi complète

---

## Principe

Architecture hexagonale (Cockburn — Ports & Adapters).
Le **Domain Core** ne connaît aucun framework, aucun canal, aucun provider LLM.
Tout ce qui est spécifique (Telegram, Discord, Claude, Ollama) est une **Adapter** derrière un **Port**.

---

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────────┐
│                        INBOUND ADAPTERS                         │
│         Telegram │ Discord │ Signal │ HTTP │ CLI                │
└──────────────────────────┬──────────────────────────────────────┘
                           │ normalize() → InboundMessage
┌──────────────────────────▼──────────────────────────────────────┐
│                      INBOUND PORT                               │
│              InboundMessage (frozen dataclass)                  │
│    { text, user_id, platform, trust_level, attachments, … }    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                       DOMAIN CORE                               │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  GuardChain │  │    Router    │  │   SessionManager      │  │
│  │  AuthGuard  │  │  commands →  │  │   compaction          │  │
│  │  RateLimit  │  │  plugins →   │  │   context             │  │
│  │  BlockGuard │  │  LLM         │  │   memory              │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   StreamProcessor                        │   │
│  │   reçoit: LlmEvent (text | tool_use | result)            │   │
│  │   émet:   RenderEvent (TextEvent | ToolSummaryEvent |    │   │
│  │                         ResultEvent)                     │   │
│  │   logique: seuils, groupage, throttle — channel-agnostic │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                        LLM PORT                                 │
│   Protocol LlmProvider:                                         │
│     complete(…) → LlmResult          (batch, sans streaming)    │
│     stream(…)   → AsyncIter[LlmEvent] (streaming tool-aware)   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ implement
┌──────────────────────────▼──────────────────────────────────────┐
│                       LLM ADAPTERS                              │
│         AnthropicSdkAdapter │ ClaudeCliAdapter │ OllamaAdapter  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ LlmEvent stream
┌──────────────────────────▼──────────────────────────────────────┐
│                    OUTBOUND PORT                                 │
│   AsyncIterator[RenderEvent]                                    │
│   RenderEvent = TextEvent | ToolSummaryEvent | ResultEvent      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   OUTBOUND DISPATCHER                           │
│   route vers le bon adapter selon platform                      │
│   throttle editMessage (rate-limit Telegram 30/min)             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                     OUTBOUND ADAPTERS                           │
│         Telegram │ Discord │ Signal │ HTTP │ CLI                │
│   implémentent: send(RenderEvent) — chacun à sa façon           │
│   Telegram: editMessage en place                                │
│   Discord:  update embed                                        │
│   CLI:      print coloré                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Types canoniques

### LlmEvent (émis par les LLM adapters)

```python
@dataclass(frozen=True)
class TextLlmEvent:
    text: str

@dataclass(frozen=True)
class ToolUseLlmEvent:
    tool_name: str          # "Edit" | "Read" | "Bash" | "Grep" | …
    tool_id: str
    input: dict             # paramètres bruts du tool

@dataclass(frozen=True)
class ResultLlmEvent:
    is_error: bool
    duration_ms: int
    cost_usd: float | None

LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent
```

### RenderEvent (émis par le StreamProcessor)

```python
@dataclass(frozen=True)
class TextRenderEvent:
    text: str
    is_final: bool          # True = dernier message de la session

@dataclass(frozen=True)
class ToolSummaryRenderEvent:
    """Snapshot courant du résumé des tools — remplace le précédent."""
    files: dict[str, FileEditSummary]   # file_path → edits
    bash_commands: list[str]
    silent_counts: SilentCounts         # reads, greps, globs
    is_complete: bool                   # True = result reçu

@dataclass(frozen=True)
class FileEditSummary:
    path: str
    edits: list[str]        # noms de fonctions (si ≤ threshold)
    count: int              # total edits sur ce fichier

@dataclass(frozen=True)
class SilentCounts:
    reads: int
    greps: int
    globs: int

RenderEvent = TextRenderEvent | ToolSummaryRenderEvent
```

---

## StreamProcessor — logique

```
Pour chaque LlmEvent reçu:

  TextLlmEvent   → émet TextRenderEvent(text, is_final=False)
                   sauf si le texte est une "intro courte" (se termine par ":")
                   et qu'un ToolUseLlmEvent suit dans le même turn
                   → dans ce cas, fusionner avec le ToolSummaryRenderEvent

  ToolUseLlmEvent →
    Edit / Write → accumuler dans files[path].edits
                   si len(edits) > names_threshold → passer en mode count
                   si len(files) >= group_threshold → grouper par fichier
    Bash         → accumuler dans bash_commands (tronqué à bash_max_len)
    Read/Grep/Glob → silent_counts++
    WebFetch     → accumuler domaine (si show.web_fetch)
    Agent        → event spécial (si show.agent)
    → émet ToolSummaryRenderEvent (snapshot courant)

  ResultLlmEvent → émet ToolSummaryRenderEvent(is_complete=True)
                   puis TextRenderEvent(dernier texte, is_final=True)
```

### Config (dans `config.toml`)

```toml
[tool_display]
names_threshold = 3     # edits avant bascule vers count par fichier
group_threshold = 3     # fichiers avant groupage
bash_max_len    = 60    # chars max pour la commande bash
throttle_ms     = 2000  # délai min entre deux outbound updates

[tool_display.show]
read       = false
glob       = false
grep       = false
web_fetch  = true
web_search = true
agent      = true
bash       = true
write      = true
edit       = true
```

---

## Outbound Adapters — responsabilités

Chaque outbound adapter reçoit un `AsyncIterator[RenderEvent]` et décide comment le rendre.

**TelegramOutbound:**
- `TextRenderEvent` → `sendMessage` si is_final, sinon `⏳ texte`
- `ToolSummaryRenderEvent` → `sendMessage` au premier, puis `editMessage` (throttlé)
- `ToolSummaryRenderEvent(is_complete=True)` → edit final avec ✅

**DiscordOutbound:**
- `ToolSummaryRenderEvent` → update embed dans le message en cours
- `TextRenderEvent(is_final=True)` → nouveau message ou suite de l'embed

**CliOutbound:**
- Print coloré ligne par ligne, pas d'edit

---

## Invariants

- Le Domain Core n'importe rien de `aiogram`, `discord`, `anthropic`, `httpx`
- `StreamProcessor` est testable en isolation (pas de réseau)
- Un nouvel outbound adapter n'a besoin de connaître que `RenderEvent`
- Un nouveau LLM adapter n'a besoin d'implémenter que `stream() → AsyncIter[LlmEvent]`
