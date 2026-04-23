# Gap Analysis — Current → Target Architecture

> Date: 2026-03-19
> Refs: current-state.md · target-architecture.md

---

## Vue d'ensemble des gaps

```
                    CURRENT                     TARGET
                    ───────                     ──────
LLM stream     complete() → LlmResult      stream() → AsyncIter[LlmEvent]
               (buffer complet)            (tool_use exposés en temps réel)

Domain core    rien entre LLM et outbound  StreamProcessor (channel-agnostic)

Outbound type  AsyncIterator[str]          AsyncIterator[RenderEvent]

Config         aucune pour tool display    [tool_display] dans config.toml
```

---

## Gap 1 — LlmProvider.stream() manquant

**Problème :** `LlmProvider` n'a que `complete()` qui retourne un `LlmResult` bufferisé.
Les `tool_use` events ne sont jamais exposés à l'appelant.

**Correction :**

Ajouter `stream()` au protocol `LlmProvider` :
```python
# llm/base.py
class LlmProvider(Protocol):
    async def complete(…) -> LlmResult: ...        # existant — garder
    async def stream(…) -> AsyncIterator[LlmEvent]: ...  # nouveau
```

Implémenter dans `ClaudeCliDriver` :
```python
# llm/drivers/cli.py — lire content[] du NDJSON
async def stream(self, …):
    async for line in self._pool.send_stream(…):
        event = json.loads(line)
        if event["type"] == "assistant":
            for block in event["message"]["content"]:
                if block["type"] == "text":
                    yield TextLlmEvent(text=block["text"])
                elif block["type"] == "tool_use":
                    yield ToolUseLlmEvent(
                        tool_name=block["name"],
                        tool_id=block["id"],
                        input=block["input"],
                    )
        elif event["type"] == "result":
            yield ResultLlmEvent(
                is_error=event.get("is_error", False),
                duration_ms=event.get("duration_ms", 0),
                cost_usd=event.get("cost_usd"),
            )
```

**Complexité :** M
**Fichiers :** `llm/base.py`, `llm/drivers/cli.py`
**Rétro-compatibilité :** `complete()` reste intact — migration optionnelle

---

## Gap 2 — Types LlmEvent manquants

**Correction :** Nouveau fichier `llm/events.py`

```python
# llm/events.py
from dataclasses import dataclass

@dataclass(frozen=True)
class TextLlmEvent:
    text: str

@dataclass(frozen=True)
class ToolUseLlmEvent:
    tool_name: str
    tool_id: str
    input: dict

@dataclass(frozen=True)
class ResultLlmEvent:
    is_error: bool
    duration_ms: int
    cost_usd: float | None

LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent
```

**Complexité :** S — pure définition de types

---

## Gap 3 — StreamProcessor manquant (le plus important)

**Problème :** Aucune couche domain entre le LLM et l'outbound.
La logique "quoi montrer, comment regrouper" est absente.

**Correction :** Nouveau fichier `core/stream_processor.py`

Responsabilités :
- Reçoit `AsyncIterator[LlmEvent]`
- Maintient l'état interne (files accumulés, bash commands, silent counts)
- Applique les seuils de config (names_threshold, group_threshold, etc.)
- Émet `AsyncIterator[RenderEvent]`
- **Ne connaît pas Telegram, Discord, ni aucun framework**

```python
# core/stream_processor.py (squelette)
class StreamProcessor:
    def __init__(self, config: ToolDisplayConfig): ...

    async def process(
        self, events: AsyncIterator[LlmEvent]
    ) -> AsyncIterator[RenderEvent]:
        async for event in events:
            match event:
                case TextLlmEvent(text=t):
                    yield TextRenderEvent(text=t, is_final=False)
                case ToolUseLlmEvent():
                    self._accumulate(event)
                    yield ToolSummaryRenderEvent(snapshot=self._snapshot())
                case ResultLlmEvent():
                    yield ToolSummaryRenderEvent(snapshot=self._snapshot(), is_complete=True)
                    if self._pending_text:
                        yield TextRenderEvent(text=self._pending_text, is_final=True)
```

**Complexité :** M-L (logique d'accumulation + seuils + tests)
**Fichiers :** `core/stream_processor.py` (nouveau), `core/render_events.py` (nouveau)

---

## Gap 4 — RenderEvent types manquants

**Correction :** Nouveau fichier `core/render_events.py`

```python
@dataclass(frozen=True)
class TextRenderEvent:
    text: str
    is_final: bool

@dataclass(frozen=True)
class ToolSummaryRenderEvent:
    files: dict[str, FileEditSummary]
    bash_commands: list[str]
    silent_counts: SilentCounts
    is_complete: bool

RenderEvent = TextRenderEvent | ToolSummaryRenderEvent
```

**Complexité :** S

---

## Gap 5 — ChannelAdapter.send_streaming() prend str pas RenderEvent

**Problème :**
```python
# hub_protocol.py — actuel
async def send_streaming(self, original_msg, chunks: AsyncIterator[str], …) -> None: ...
```

**Correction :**
```python
# hub_protocol.py — cible
async def send_streaming(
    self, original_msg: InboundMessage,
    events: AsyncIterator[RenderEvent],
    outbound: OutboundMessage | None = None,
) -> None: ...
```

Mettre à jour les implémentations :

`telegram_outbound.py` :
```python
async def send_streaming(self, original_msg, events, outbound=None):
    msg_id = None
    async for event in events:
        match event:
            case ToolSummaryRenderEvent() if not msg_id:
                sent = await bot.send_message(chat_id, render_tool_summary(event))
                msg_id = sent.message_id
            case ToolSummaryRenderEvent() if msg_id:
                await bot.edit_message_text(render_tool_summary(event), …, msg_id)
                await asyncio.sleep(throttle_ms / 1000)
            case TextRenderEvent(is_final=True):
                await bot.send_message(chat_id, event.text)
```

`discord_outbound.py` : idem avec update embed.

**Complexité :** M
**Fichiers :** `core/hub_protocol.py`, `adapters/telegram_outbound.py`, `adapters/discord_outbound.py`, `adapters/cli.py`

---

## Gap 6 — Config tool_display absente

**Correction :** Ajouter dans `config.toml.example` et `bootstrap/config.py`

```toml
[tool_display]
names_threshold = 3
group_threshold = 3
bash_max_len    = 60
throttle_ms     = 2000

[tool_display.show]
edit       = true
write      = true
bash       = true
web_fetch  = true
web_search = true
agent      = true
read       = false
grep       = false
glob       = false
```

**Complexité :** S

---

## Stratégie de correction

### Ordre d'implémentation

```
Phase 1 — Types (S, aucun risque)
  ├─ llm/events.py                 LlmEvent types
  └─ core/render_events.py         RenderEvent types

Phase 2 — LLM drivers (M, isolé)
  ├─ llm/base.py                   ajouter stream() au Protocol
  └─ llm/drivers/cli.py            implémenter stream()

Phase 3 — Domain Core (M-L, cœur)
  └─ core/stream_processor.py      StreamProcessor + ToolDisplayConfig

Phase 4 — Outbound adapters (M, par adapter)
  ├─ core/hub_protocol.py          send_streaming(AsyncIter[RenderEvent])
  ├─ adapters/telegram_outbound.py render ToolSummaryRenderEvent
  └─ adapters/discord_outbound.py  render ToolSummaryRenderEvent

Phase 5 — Config + wiring (S)
  ├─ config.toml.example           [tool_display]
  └─ bootstrap/config.py           parse ToolDisplayConfig
```

### Principes

- `complete()` reste intact — zéro régression sur le path actuel
- Chaque phase est mergeable indépendamment
- `StreamProcessor` couvert à 100% par des tests unitaires (pas de réseau)
- Les adapters outbound peuvent implémenter `send_streaming(RenderEvent)` progressivement — fallback sur str si RenderEvent inconnu

### Tests à écrire

- `StreamProcessor` : 10-15 cas (1 edit, 5 edits, 12 edits, 80 tools, multi-fichiers, text seul, bash, read silencieux)
- `ClaudeCliDriver.stream()` : mock NDJSON, vérifier parsing tool_use blocks
- `TelegramOutbound.send_streaming()` : mock bot API, vérifier editMessage throttlé
