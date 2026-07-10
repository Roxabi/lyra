# TanStack AI — playbook d'adoption : quoi prendre, où s'en servir

> Companion de `artifacts/analyses/2026-07-08-tanstack-ai-eval.claude.md` (le verdict). Ici : le **quoi exactement** + le **où précisément**, fichier par fichier. Facts vérifiés au préalable (workflow multi-agents, 8 claims load-bearing) ; grounding code relu le 2026-07-09.

## Principe directeur

On prend **le contrat** (protocole AG-UI) + **le renderer** (`useChat`). On **ne prend PAS** l'engine (agent-loop / providers / tools / MCP / media) — la factory les possède déjà côté Python. La ligne de partage :

```
  SERVEUR (Python, on possède)                         CLIENT (TS, on adopte)
  ─────────────────────────────                        ──────────────────────
  StreamProcessor → RenderEvent  ──[NEW serializer]──►  SSE AG-UI  ──►  ConnectConnectionAdapter
   (déjà émis, ADR-070)            web_formatter.py      /api/stream        (bridge 2-temps)
                                   chat_routes.py                                 │
                                                                                  ▼
                                                                          ChatClient (@tanstack/ai-client)
                                                                                  │
                                                                                  ▼
                                                                          useChat() → message.parts
                                                                          (TextPart / ThinkingPart / ToolCallPart)
```

Tout ce qui est à gauche reste Python et autoritaire (routing LLM, exécution tools, authz hub Stage-7). AG-UI ne transporte QUE le rendu — il ne tire rien de tout ça côté client.

---

## 0. Les 4 choses à prendre (vue d'ensemble)

| # | À prendre | Package / artefact | Où ça vit | Verdict |
|---|---|---|---|---|
| 1 | **Protocole AG-UI** (le vocabulaire d'events) | `@ag-ui/core` (TS) + `ag-ui-protocol` (Python, Pydantic + `EventEncoder`) | contrat partagé : serializer Python + client TS | **ADOPT** |
| 2 | **Serializer edge AG-UI** | code maison qui émet des events AG-UI | `src/factory/adapters/web/` | **ADOPT** (Step 1, want-anyway) |
| 3 | **`useChat` + `ConnectConnectionAdapter`** | `@tanstack/ai-client` + `@tanstack/ai-react` | `apps/dashboard-v2/src/features/chat/` | **ADOPT** (Step 2, v2) |
| 4 | **Idées d'observabilité** (devtools timeline, fixture replay, contentGuard, otel span hierarchy, per-model typing) | patterns, pas la lib | dashboard v2 + stages Python | **BORROW-IDEA** |

**Ce qu'on NE prend PAS** (détaillé §6) : provider adapters, isomorphic/client tools, MCP, media/voice, l'engine `chat()`.

---

## 1. À prendre #1 — le protocole AG-UI (le contrat de wire)

**Quoi.** AG-UI est un standard ouvert d'events pour le streaming agent↔UI (`@ag-ui/core`, maintenu par ag-ui-protocol/copilotkit — **pas** un fork TanStack). TanStack AI l'utilise nativement : son type on-wire `StreamChunk = AGUIEvent` (superset additif). Il existe un **SDK Python officiel** `ag-ui-protocol` (PyPI) : modèles Pydantic `ag_ui.core.*` + `ag_ui.encoder.EventEncoder` qui sérialise en SSE. ⇒ le serveur Python émet de l'AG-UI natif, le client TS le consomme natif, **zéro shim JS** (contra Vercel AI SDK qui exige `@ag-ui/vercel-ai-sdk`).

**Pourquoi c'est quasi-gratuit chez nous.** La factory a **déjà back-porté les 4 familles AG-UI** en interne comme `RenderEvent` (ADR-070, `src/factory/core/messaging/render_events.py`). Le doc dit verbatim *« AG-UI is not adopted as a wire format »* + nomme un *« AG-UI HTTP/SSE adapter »* planifié. On ne crée pas de domain model — on **sérialise ce qui existe déjà**.

**Où.** Contrat partagé. Un seul point de traduction Python (§2) + le client parle AG-UI transitivement via `@tanstack/ai-react` (§3). Une seule schema AG-UI aux deux bouts.

### Table de correspondance (le cœur du travail Step 1)

`RenderEvent` interne (classe + champs exacts) → event AG-UI (nom + champs camelCase exacts) → ce que l'edge web émet **aujourd'hui** (ad-hoc).

| RenderEvent (Python, `render_events.py`) | Event AG-UI (`type` + champs) | Ad-hoc actuel |
|---|---|---|
| `RunStartedRenderEvent(run_id)` | `RUN_STARTED {threadId, runId}` | — (absent) |
| `TextStartRenderEvent(message_id, role)` | `TEXT_MESSAGE_START {messageId, role:"assistant"}` | — (absent) |
| `TextDeltaRenderEvent(message_id, delta)` | `TEXT_MESSAGE_CONTENT {messageId, delta}` | `{type:"delta", text}` **(buffer cumulatif, pas delta)** |
| `TextEndRenderEvent(message_id)` | `TEXT_MESSAGE_END {messageId}` | — (absent) |
| `ReasoningStartRenderEvent(message_id)` | `REASONING_START {messageId}` (+ `REASONING_MESSAGE_START {messageId, role}`) | **jeté** (`edit_reasoning` no-op) |
| `ReasoningDeltaRenderEvent(message_id, delta)` | `REASONING_MESSAGE_CONTENT {messageId, delta}` | **jeté** |
| `ReasoningEndRenderEvent(message_id)` | `REASONING_MESSAGE_END {messageId}` (+ `REASONING_END {messageId}`) | **jeté** |
| `ToolCallStartRenderEvent(tool_call_id, tool_name, input)` | `TOOL_CALL_START {toolCallId, toolCallName, parentMessageId?}` | **jeté / aplati en recap** (voir caveat §2) |
| `ToolCallArgsRenderEvent(tool_call_id, delta)` | `TOOL_CALL_ARGS {toolCallId, delta}` | **jeté / aplati** |
| `ToolCallEndRenderEvent(tool_call_id)` | `TOOL_CALL_END {toolCallId}` | **jeté / aplati** |
| `ToolCallResultRenderEvent(tool_call_id, content, is_error)` | `TOOL_CALL_RESULT {messageId, toolCallId, content}` | **jeté / aplati** |
| `RunFinishedRenderEvent(run_id, outcome)` | `RUN_FINISHED {threadId, runId}` | `{type:"done"}` |
| `RunErrorRenderEvent(run_id, message, code)` | `RUN_ERROR {message, code?}` | `{type:"error", message}` |
| keepalive (timeout) | — (pas un event AG-UI) | `{type:"ping"}` → **commentaire SSE `: ping\n\n`** |

> **À vérifier une fois puis lock** (ne pas sur-supposer) :
> - **Set exact des events reasoning** attendu par le parser TanStack (REASONING_START vs REASONING_MESSAGE_START…) → ThinkingPart. Émettre, ouvrir le devtools, ajuster.
> - **Sentinelle terminale** : TanStack ajoute parfois `data: [DONE]\n\n` que `EventEncoder` n'émet pas. Vérifier si le client attend `[DONE]` en plus de `RUN_FINISHED`, sinon l'ajouter (1 ligne).
> - **`threadId`/`runId`** : mapper `run_id` (= `trace_id` par-turn) sur `runId`; `threadId` = notre `session_id` (browser). Stable par conversation.

---

## 2. À prendre #2 — le serializer edge (SERVEUR, Python) — Step 1, à faire d'abord

C'est **le seul gap load-bearing** et un changement serveur qu'on veut de toute façon (contrat de wire explicite > dicts ad-hoc). Il est **indépendant** du choix de client.

### Où exactement

Tout dans `src/factory/adapters/web/` (axe adapter, thin — respecte l'axe stage) :

| Fichier | Méthode / fonction | Changement |
|---|---|---|
| `web_formatter.py` | `send_placeholder` / `edit_placeholder_text` | émettre `TEXT_MESSAGE_START/CONTENT/END` au lieu de `{type:"delta", text}` ; **passer en incrémental** |
| `web_formatter.py` | `edit_reasoning` **(no-op aujourd'hui)** | émettre `REASONING_*` (dé-no-op) |
| `web_formatter.py` | `edit_tool_recap` **(no-op aujourd'hui)** | émettre `TOOL_CALL_*` — **caveat ci-dessous** |
| `web_outbound.py` | `send` (chemin non-streaming) | `TEXT_MESSAGE_START/CONTENT/END` + `RUN_FINISHED` au lieu de `{delta}`+`{done}` |
| `chat_routes.py` | `event_gen()` (framing SSE) | garder `data: {json}\n\n` ; keepalive `{type:"ping"}` → commentaire `: ping\n\n` ; terminal sur `RUN_FINISHED`/`RUN_ERROR` au lieu de `done`/`error` |
| **nouveau** `web_agui.py` | `render_event_to_agui(ev) -> dict` | 1 module de traduction pur `RenderEvent → dict AG-UI` (match sur type), réutilisable par le futur AG-UI HTTP/SSE adapter |

**Dépendance** : `ag-ui-protocol` (Pydantic + `EventEncoder`) — cheap, `roxabi-contracts` est déjà Pydantic. Alternative : hand-roll ~8 shapes de dict (pas de dep). Recommandé : la dep, pour rester aligné sur la spec versionnée.

### Le fix cumulatif → incrémental (piège #1)

Aujourd'hui `WebFormatter.edit_placeholder_text` fait `self._buffer = text` et publie le **buffer complet** ; le client fait `l + ev.text` (append). OK au smoke (1 seul delta), **cassé en vrai streaming incrémental** (duplication). AG-UI `TEXT_MESSAGE_CONTENT.delta` doit être le **fragment incrémental** :

```python
# web_formatter.py — esquisse
async def edit_placeholder_text(self, ph, text, *, finalize=False):
    increment = text[len(self._buffer):]      # <-- incrémental, pas le buffer complet
    self._buffer = text
    if increment:
        await self._publish(agui.TextMessageContent(message_id=self._mid, delta=increment))
    if finalize:
        await self._publish(agui.TextMessageEnd(message_id=self._mid))
        await self._publish(agui.RunFinished(thread_id=self._session_id, run_id=self._run_id))
```

(`_mid`/`_run_id` mintés au `send_placeholder` → `TEXT_MESSAGE_START` + `RUN_STARTED`.)

### Caveat tool-call (piège #2 — ne PAS sur-croire « 2 fichiers, no stage changes »)

- **Text + Reasoning** arrivent au formatter comme **objets structurés** (`edit_reasoning` reçoit l'event verbatim) ⇒ vrai swap de sérialiseur **edge-only**, faisable tout de suite.
- **Tool-calls** : l'`OutboundEmitter` **partagé** (`src/factory/outbound/_emitter_run.py`, `emitter.py`) **aplatit** `ToolCall{Start,Args,End,Result}` en **recap strings** (`ToolRecapAccumulator` → `format_recap_lines`) **avant** le formatter. `WebFormatter.edit_tool_recap` ne reçoit que `list[str]`, jamais les events structurés. ⇒ fidélité tool-call complète = soit
  - (a) changer le **stage emitter partagé** (touche Telegram/Discord — coûteux, cross-adapter), soit
  - (b) le **« AG-UI HTTP/SSE adapter » différé** (ADR-070) qui s'abonne au **flux `RenderEvent` brut** via `NatsOutboundListener` (les tool events structurés sont déjà sur le bus NATS, avant l'aplatissement).

  **Reco** : Step 1 = Text + Reasoning + Run-lifecycle (edge-only, gratuit). Tool-call fidélité = lot séparé via l'option (b), pas dans le premier lot. Au pire, émettre les recap lines comme un `TOOL_CALL_RESULT.content` dégradé en attendant.

### Séquencement anti-flag-day (piège #3 — ne pas casser v1/v2 actuels)

Changer le vocabulaire sur `/api/stream/{id}` **casse** les clients raw-EventSource existants (v1 Astryx + v2 actuel attendent `{type:delta}`). Ne PAS flipper le wire d'un coup. Négocier :

```
GET /api/stream/{session_id}?token=...&format=agui     # opt-in AG-UI (nouveau client v2)
GET /api/stream/{session_id}?token=...                  # legacy {type:delta} par défaut (v1, inchangé)
```

`event_gen()` branche sur `format` (ou header `Accept: text/event-stream; profile=agui`). v1 continue de tourner, v2 opte pour AG-UI, v1 retiré plus tard. Le handshake mint/verify (`stream_tokens.py`) est **inchangé** dans les deux cas → posture sécu identique (#2262 orthogonal).

---

## 3. À prendre #3 — `useChat` + `ConnectConnectionAdapter` (CLIENT, dashboard-v2) — Step 2

**Statut réel de v2** (relu 2026-07-09) : `apps/dashboard-v2/src/features/chat/` est un **port fonctionnel de v1** — `api.ts openChatStream` (raw EventSource) + `chat-pane.tsx` avec `sourceRef`/`setLog((l)=>l+ev.text)` + `message-list.tsx`/`message-bubble.tsx` rendant un **`log` string plat**. Ce n'est donc pas du net-new : c'est un **refactor** de la feature chat existante.

### Où exactement + quoi

| Fichier v2 | Aujourd'hui | Après |
|---|---|---|
| **nouveau** `features/chat/agui-adapter.ts` | — | `ConnectConnectionAdapter` custom (bridge 2-temps) |
| `features/chat/api.ts` | `openChatStream` (raw EventSource) | **supprimer** `openChatStream` (gardé `postChat`/`fetchAgents`/`fetchSessions`…) |
| `features/chat/components/chat-pane.tsx` | `sourceRef` + `setLog` + `connectStream` | `useChat({ connection })` → `messages` / `sendMessage` / `status` / `stop` |
| `features/chat/components/message-list.tsx` | prend `log: string` | prend `messages: UIMessage[]`, rend `message.parts` |
| `features/chat/components/message-bubble.tsx` | texte plat | switch sur part : TextPart / **ThinkingPart** (collapsible) / **ToolCallPart** (card) |
| `dev-mock/plugin.ts` | mock SSE `{type:delta/done}` | **doit aussi émettre AG-UI** (sinon `bun dev` par défaut casse — cf. proxy gated `DASHBOARD_MOCK=0`) |

**Deps à ajouter** (`apps/dashboard-v2/package.json`) : `@tanstack/ai`, `@tanstack/ai-client`, `@tanstack/ai-react`. (`@tanstack/ai-client` est **standalone**, pas de dep obligatoire sur l'engine — on ne tire pas `chat()`.)

### Le custom adapter (bridge le handshake 2-temps)

Le `fetchServerSentEvents` par défaut de TanStack suppose **un seul POST `RunAgentInput`**. Notre contrat = **2 temps** (`POST /api/chat` → `{session_id, stream_token}`, puis `GET /api/stream/{id}?token=`). D'où un `ConnectConnectionAdapter` custom (~1 fichier) :

```typescript
// features/chat/agui-adapter.ts — esquisse (signature réelle: connect(messages, data?, abortSignal?, runContext?): AsyncIterable<StreamChunk>)
import type { ConnectConnectionAdapter } from "@tanstack/ai-react";
import type { StreamChunk } from "@tanstack/ai";
import { postChat } from "@/features/chat/api";

export function makeAguiAdapter(tab: { agent: string; harness: HarnessKind; model: string; sessionId?: string | null }): ConnectConnectionAdapter {
  return {
    async *connect(messages, data, abortSignal) {
      const text = lastUserText(messages);                       // dernier message user
      // 1) POST /api/chat (pipeline inbound + mint token) — contrat inchangé
      const { session_id, stream_token } = await postChat({
        agent: tab.agent, text, session_id: tab.sessionId, harness: tab.harness, model: tab.model,
      });
      // 2) GET SSE AG-UI, parse, yield chaque event comme StreamChunk
      const res = await fetch(`/api/stream/${session_id}?token=${encodeURIComponent(stream_token)}&format=agui`, { signal: abortSignal });
      const reader = res.body!.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        for (const frame of splitSSE(buf)) {                     // split \n\n, strip "data: ", skip ": ping"
          yield JSON.parse(frame) as StreamChunk;                // = event AG-UI
        }
        buf = tailAfterLastFrame(buf);
      }
    },
  };
}
```

### chat-pane.tsx — avant / après

```typescript
// AVANT : raw EventSource + string log
sourceRef.current = openChatStream(sessionId, streamToken, (ev) => {
  if (ev.type === "delta") setLog((l) => l + (ev.text ?? ""));   // cumulatif-bug latent
  if (ev.type === "done") setLog((l) => `${l}\n---\n`);
});

// APRÈS : useChat — parts structurés, stop, status gratis
const { messages, sendMessage, status, stop } = useChat({ connection: makeAguiAdapter(tab) });
// <MessageList messages={messages} /> rend message.parts : TextPart / ThinkingPart / ToolCallPart
// composer.onSend = () => sendMessage(text)
```

**Ce qu'on gagne** : modèle de messages par rôle + `message.parts` (thinking collapsible + tool cards) au lieu d'un `log` concaténé ; `status`/`isLoading` ; `stop()` ; suppression du bug cumulatif. **Ce qu'on NE branche PAS** : `clientTools`, provider typing, media (opt-in, tree-shakeable, laissés off). Les streams **jobs/pipeline** (`features/jobs/api.ts`, `features/pipeline/api.ts`, `shared/hooks/use-*-live.ts`) restent en **raw EventSource** — ce sont des snapshots broadcast, pas des runs AG-UI. Ne pas y toucher.

---

## 4. À prendre #4 — les idées d'observabilité (sans la lib)

| Idée TanStack | Mécanisme | Où l'appliquer chez nous |
|---|---|---|
| **Devtools timeline** | panel dev-only : run/thread timeline, I/O des tools par `runId` | nouveau panneau debug dans dashboard-v2 (dev-only) branché sur le flux AG-UI qu'on émet déjà — introspection interaction **côté dashboard** (aujourd'hui : trace serveur forte, zéro côté client) |
| **Fixture replay** | payloads AG-UI stockés en localStorage → dev sans backend live | dev-mock v2 : rejouer des transcripts AG-UI capturés (dev chat sans M₁) |
| **`contentGuardMiddleware`** | redaction egress sur `TEXT_MESSAGE_CONTENT` | **nouveau stage egress-redaction** côté Python (la factory n'en a pas) — sur l'axe stage, pas par adapter |
| **otel span hierarchy** (call→loop→tool) | spans GenAI hiérarchiques | shape pour aligner notre `TraceContext`/otel/Loki (déjà riche) sur une hiérarchie call→loop→tool lisible |
| **Per-model capability typing** | `openaiText<TModel>` + capability-map, rejet compile-time | **seulement si** v2 expose un jour un model-picker typé forwardant vers le router Python. Sinon N/A (provider selection reste server-side) |

---

## 5. Ce qu'on NE prend PAS (et pourquoi)

| Non-pris | Pourquoi (ancré factory) |
|---|---|
| **Provider adapters** (`@tanstack/ai-openai/anthropic/…`) + per-model typing | Routing LLM 100% server-side Python (LiteLLM/llmCLI + GPU local + clipool Claude CLI). Le client ne choisit jamais un provider. **REDUNDANT.** |
| **Isomorphic / client tools** (`.client()`) | Tous les tools exécutés server-side + autorisés au hub **Stage-7** (`agent_grants` deny-by-default, ACL NKey). `.client()` **bypasserait Stage-7** = régression sécu. Seul le *rendering* (ToolCallPart) a de la valeur (pris en §1-3). **Sûr par discipline : ne jamais register de tool `.client()`/frontend.** |
| **L'engine `chat()`** (agent-loop TS) | Il n'y a **pas** de serveur Node/TS dans la topo pour l'héberger ; le monter = boucle parallèle concurrente qui bypasse le routing Python. **MISALIGNED.** |
| **MCP wiring** | `createMCPClient` = server-side TS only → orphelin comme les server-tools. Orchestration MCP appartiendrait à la boucle Python. **SKIP.** |
| **Media / realtime voice** | voiceCLI possède TTS/STT GPU local ; web `supports_audio=False`, egress audio **ACL-denied by design**. Adapters TanStack = cloud direct → bypasseraient voiceCLI + authz. **SKIP** (pattern `useGeneration` = référence seulement). |
| **Approval / HITL** (moitié) | Utile comme **idée** (shape d'event + widget) mais la moitié serveur (resume) = à ré-implémenter en Python ; axe ≠ grant matrix. **BORROW-IDEA**, pas la lib. |

---

## 6. Séquencement (lots `/dev`)

| Lot | Portée | Fichiers | Casse qqch ? |
|---|---|---|---|
| **1 — serializer edge (server)** | `RUN_*` + `TEXT_MESSAGE_*` + `REASONING_*` en AG-UI, opt-in `?format=agui`, incrémental, ping→commentaire | `web_formatter.py`, `web_outbound.py`, `chat_routes.py`, nouveau `web_agui.py` | Non (legacy par défaut) |
| **2 — client v2** | `useChat` + `agui-adapter.ts` + parts rendering + mock AG-UI | `features/chat/*`, `dev-mock/plugin.ts`, `package.json` | Non (v1 sur legacy) |
| **3 — tool-call fidélité** (option) | AG-UI HTTP/SSE adapter sur flux `RenderEvent` brut (option (b)) → `TOOL_CALL_*` complets, parité web↔TG/DC | nouveau adapter s'abonnant à `NatsOutboundListener` | Non |
| **4 — idées obs** (option) | devtools panel, fixture replay, stage contentGuard | dashboard-v2 + nouveau stage Python | Non |

**Faire Lot 1 d'abord** : c'est du serveur qu'on veut même si le client reste hand-rolled (contrat de wire explicite, reasoning/tool arrêtent d'être jetés). Lot 2 devient un follow-on quasi-gratuit. Ne PAS parier sur le framework complet.

---

## 7. Gotchas (recap)

1. **Cumulatif→incrémental** : `TEXT_MESSAGE_CONTENT.delta` = fragment, pas le buffer. Fixer `WebFormatter._buffer`.
2. **Tool-call ≠ edge-only** : l'emitter partagé aplatit en recap avant le formatter → Lot 3 via le flux RenderEvent brut, pas un swap 2-fichiers.
3. **Anti-flag-day** : `?format=agui` opt-in, ne pas flipper le wire (casse v1/v2 actuels).
4. **`.client()` interdit** : sûr par discipline, pas par protocole (sinon bypass Stage-7).
5. **Dev mock** : `dev-mock/plugin.ts` doit émettre AG-UI aussi (mode `dev` par défaut ne tape pas M₁).
6. **#2262** (BFF unauth tailnet) : orthogonal, à fixer de toute façon ; le bridge préserve `stream_token`, n'aggrave pas.
7. **À lock après 1 test** : set exact events reasoning + sentinelle `[DONE]` vs `RUN_FINISHED`.

## Deps

- **Python** : `ag-ui-protocol` (PyPI) — `ag_ui.core` (Pydantic events) + `ag_ui.encoder.EventEncoder`.
- **TS** (`apps/dashboard-v2`) : `@tanstack/ai`, `@tanstack/ai-client`, `@tanstack/ai-react` (tire `@ag-ui/core` transitivement). Optionnel dev : `@tanstack/react-ai-devtools`.
