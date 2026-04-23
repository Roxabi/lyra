# Current State — Architecture à date

> Date: 2026-03-19
> Basé sur: exploration complète de `src/lyra/`

---

## Ce qui existe

### Ports — partiellement définis

**`ChannelAdapter` Protocol** (`core/hub_protocol.py`) ✅
```python
class ChannelAdapter(Protocol):
    def normalize(self, raw: Any) -> InboundMessage: ...
    async def send(self, original_msg, outbound: OutboundMessage) -> None: ...
    async def send_streaming(self, original_msg, chunks: AsyncIterator[str], ...) -> None: ...
    async def render_audio(…) -> None: ...
    async def render_attachment(…) -> None: ...
```

**`LlmProvider` Protocol** (`llm/base.py`) ✅ partiel
```python
class LlmProvider(Protocol):
    async def complete(
        self, pool_id, text, model_cfg, system_prompt,
        messages=None,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> LlmResult: ...
    def is_alive(self, pool_id) -> bool: ...
```

**`InboundMessage`** (`core/message.py`) ✅ bien défini
- Frozen dataclass, typé, sécurisé
- Contient: text, user_id, platform, trust_level, attachments, routing

**`OutboundMessage`** (`core/message.py`) ✅ partiel
- `content: list[ContentPart]` où `ContentPart = str | CodeBlock | MediaPart`
- Pas de `ToolSummaryContent` — les tool events n'ont pas de représentation dans le type

---

### Inbound — bien implémenté

- **InboundBus** (`core/inbound_bus.py`) ✅
  - File par plateforme (maxsize=100) + staging queue (500)
  - Feeder tasks par plateforme
  - `bus.put(Platform.TELEGRAM, msg)` → staging → `Hub.run()`

- **TelegramAdapter** (`adapters/telegram.py`) ✅
  - aiogram v3 webhook
  - `telegram_normalize.normalize()` → `InboundMessage`
  - Délègue à `telegram_inbound.py`

- **DiscordAdapter** (`adapters/discord.py`) ✅
  - discord.py v2 gateway
  - `discord_normalize.normalize()` → `InboundMessage`
  - Délègue à `discord_inbound.py`

---

### Domain Core — partiel

- **GuardChain / Authenticator** (`core/guard.py`, `core/authenticator.py`) ✅
- **Router / CommandRouter** (`core/command_router.py`) ✅
- **SessionManager / Pool** (`core/pool.py`, `core/pool_manager.py`) ✅
- **Memory 5 niveaux** (`core/memory.py`) ✅ L0/L1/L3 opérationnels
- **SmartRouting** (`llm/smart_routing.py`) ✅ complexité → modèle

**❌ StreamProcessor** — n'existe pas
- Aucune agrégation des `tool_use` events
- Aucun type `LlmEvent` ni `RenderEvent`
- Aucune logique de seuils, groupage, throttle au niveau domain

---

### LLM Adapters — partiellement streamés

**`ClaudeCliDriver`** (`llm/drivers/cli.py`) — ⚠️ stream text only
- Lit le NDJSON subprocess
- Passe les blocs `type=="text"` au callback `on_intermediate`
- Les blocs `type=="tool_use"` dans `content[]` sont ignorés / filtrés

---

### Outbound — couplé au texte

**`OutboundDispatcher`** (`core/outbound_dispatcher.py`) ✅ structure
- File par plateforme + circuit breaker
- `enqueue(InboundMessage, OutboundMessage)` → worker loop

**`telegram_outbound.py`** ⚠️ text-only streaming
```python
async def send_streaming(self, original_msg, chunks: AsyncIterator[str], …):
    # Reçoit des str, fait editMessage à chaque chunk
    # Pas de notion de ToolSummaryRenderEvent
```

**`discord_outbound.py`** — idem, `AsyncIterator[str]` uniquement

---

## Ce qui manque (résumé)

```
LlmProvider.stream() → AsyncIter[LlmEvent]       ❌ non défini
LlmEvent (TextLlmEvent | ToolUseLlmEvent | …)     ❌ non défini
StreamProcessor                                    ❌ non défini
RenderEvent (TextRenderEvent | ToolSummaryEvent)  ❌ non défini
OutboundMessage.ToolSummaryContent                ❌ non défini
ChannelAdapter.send_streaming(AsyncIter[RenderEvent]) ❌ prend str
Config tool_display (seuils, show flags)          ❌ non défini
ClaudeCliDriver expose tool_use events            ❌ filtré
```

---

## Flow actuel (réel)

```
InboundMessage
    ↓
Hub → MessagePipeline → Pool
    ↓
SimpleAgent._process_llm()
    ↓
LlmProvider.complete()          ← buffer complet, tool_use invisible
    ↓
LlmResult(result: str)          ← texte final uniquement
    ↓
OutboundDispatcher.enqueue()
    ↓
ChannelAdapter.send(OutboundMessage)   ← contenu = [str]
    ↓
Telegram sendMessage / Discord send
```

Le streaming intermédiaire (`send_streaming`) existe mais passe des `str` —
pas de tool events, pas de ToolSummary, pas d'editMessage progressif.
