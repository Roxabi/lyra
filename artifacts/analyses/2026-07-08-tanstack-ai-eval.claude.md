# TanStack AI — évaluation pour roxabi-factory

> Analyse 2026-07-08 · méthode multi-agents (4 deep-dives parallèles → vérification adversariale de 8 claims load-bearing → synthèse). Contexte : `apps/dashboard-v2/` greenfield créé le jour même, déjà dans l'écosystème TanStack (Router + Query), sans surface chat.

**TL;DR** : ne PAS acheter le framework (engine / providers / tools / media = Python-owned ou misaligned). **BORROW le protocole (AG-UI)** + **ADOPT `useChat` comme *renderer*** dans dashboard-v2 par-dessus un edge SSE AG-UI. Le gros du coût est un sérialiseur côté serveur Python que tu veux de toute façon (ADR-070 l'avait déjà prévu : « AG-UI HTTP/SSE adapter » différé).

---

## 1. Ce que fait TanStack AI + comment

- **Headless core** — `@tanstack/ai-client` = `ChatClient` framework-agnostic : possède l'état des messages, l'accumulation du stream, la réconciliation des tool-calls / reasoning en `message.parts` (TextPart / ThinkingPart / ToolCallPart).
- **Thin framework wrappers** — `useChat` (ai-react), + ai-vue/svelte/solid ré-exportent le même core, ne diffèrent que par le naming `use/create`.
- **Wire AG-UI-native** — les events server↔client *sont* des events AG-UI. `StreamChunk = AGUIEvent` (alias) ; les interfaces TanStack `extend` les types de `@ag-ui/core` et **ajoutent** des champs optionnels (`model?`) → superset additif, pas un format bespoke. Un event AG-UI de base émis par Python reste valide. **Zéro couche de traduction JS** (Vercel AI SDK, lui, exige `@ag-ui/vercel-ai-sdk`).
- **Connection adapters** — seule couche qui touche le réseau ; normalisent n'importe quel transport en StreamChunks AG-UI. Built-ins : `fetchServerSentEvents` (POST, défaut), `fetchHttpStream` (NDJSON), `stream`, `rpcStream` + `ConnectConnectionAdapter` custom (`connect()` → `AsyncIterable<StreamChunk>`).
- **Isomorphic typed tools** — `toolDefinition().server()` (tourne dans la boucle TS `chat()`) / `.client()` (tourne **dans le navigateur**) / provider-native / Code Mode. I/O typé Zod. Point dur : **la boucle agent + l'exécution des tools vivent dans un runtime TS.**
- **Middleware** — `ChatMiddleware` : `onStart / onChunk / onBeforeToolCall / onAfterToolCall / onFinish / onError / onConfig`. Built-ins : `toolCacheMiddleware`, `contentGuardMiddleware` (redaction egress), `otelMiddleware` (spans GenAI hiérarchiques call→loop→tool).
- **Devtools** — panel dev-only : Run Timeline par `threadId/runId`, inspection I/O des tools, **fixture replay** (payloads depuis schema, localStorage → dev sans backend), debug logging par catégorie.
- **Per-model typing** — `openaiText<TModel>` capture le literal du modèle ; map `ModelCapabilities` → rejet **compile-time** des options non supportées, zéro coût runtime.
- **Media** — hooks `useGenerateImage/Speech/Transcription/Video` + realtime voice ; providers **cloud** (OpenAI/ElevenLabs/Gemini/fal).
- **Approval / HITL** — tool `needsApproval:true` → event `CUSTOM approval-requested` + `RUN_FINISHED(tool_calls)` ; client `addToolApprovalResponse` → le ChatClient **re-send la conversation** pour reprendre.
- **MCP** — server-side only (`createMCPClient` dans une route serveur).

> Correction de cadrage (verdict PARTIAL vérifié) : AG-UI **n'est pas** un « render-stream » strict. Il est **bidirectionnel** et modélise l'exécution de tools **côté client** comme first-class (`RunAgentInput.tools` → `.client()` auto-exécuté dans le browser). Ça reste *permis, pas mandaté* → cf. discipline d'adoption en §4.

---

## 2. Repo setup — ce qui est bien

| Couche | Package | Rôle |
|---|---|---|
| Core agnostique | `@tanstack/ai` | engine `chat()`, middleware, tool defs, types AG-UI, adapters |
| Headless client | `@tanstack/ai-client` | ChatClient + connection adapters ; **standalone**, pas de dep obligatoire sur le core, tape n'importe quel endpoint AG-UI/SSE |
| Wrappers par framework | `@tanstack/ai-react/vue/svelte/solid` | thin, peer-dep le core, ré-exportent les mêmes helpers |
| Adapters par provider | `@tanstack/ai-openai/anthropic/gemini/...` | tree-shakeable, chacun générique sur le model literal |
| Devtools | `@tanstack/react-ai-devtools` (+ host générique) | packages **dev-only** séparés |

**Le pattern à voler :**
- **Protocol package = single source of truth.** TanStack pull `@ag-ui/core` (canonique, maintenu par ag-ui-protocol/copilotkit — **pas un fork TanStack**) plutôt que de redéfinir les events. Sibling Python `ag-ui-protocol` (Pydantic) = même spec. → interop cross-langage gratuite, zéro def d'event dupliquée à la main.
- **Headless core + thin reactive wrapper.** Le client possède la logique ; le wrapper React n'est qu'un hook.
- **Packages tree-shakeable par concern** (provider/framework/devtools séparés).

Pour NOUS : c'est exactement l'axe « thin adapters over shared contracts » de la factory (`roxabi-contracts` / `roxabi-nats`). Le move miroir pour dashboard-v2 = `@tanstack/ai-react` (parle AG-UI transitivement) côté JS + `ag-ui-protocol` (Pydantic) côté Python, **une seule schema AG-UI aux deux bouts**.

---

## 3. Fit avec la factory

| Concept TanStack | Factory aujourd'hui | Verdict | Note |
|---|---|---|---|
| **Wire AG-UI (SSE events)** | `render_events.py` a **déjà** les 4 familles AG-UI en interne (Run*, Text*, ToolCall*, Reasoning*) + codec NATS ; edge web émet ad-hoc `{delta(full-buffer)/done/error/ping}`. Doc verbatim : *« AG-UI is not adopted as a wire format »* + adapter HTTP/SSE AG-UI planifié (ADR-070). | **ADOPT** | Serializer swap, pas nouveau domain model. Seul gap load-bearing. |
| **Headless useChat renderer** | v1 `ChatPane.tsx` = raw EventSource + `setLog(l => l + ev.text)`, pas de rôles/parts/stop. v2 = **rien** (greenfield). | **ADOPT (v2)** | Corrige un bug latent : serveur envoie `delta.text` = buffer cumulatif, client fait `l + ev.text` (append) → OK au smoke, cassé en vrai streaming incrémental. |
| **Connection adapters** | 3 readers EventSource hand-rolled (chat/jobs/pipeline) dans `lib/api.ts`. | **BORROW / ADOPT partiel** | `fetchServerSentEvents` remplace `openChatStream` une fois l'edge AG-UI. Ne touche PAS jobs/pipeline (broadcast snapshots, pas des runs AG-UI). |
| **Reasoning / tool stream** | Core produit Reasoning*/ToolCall*, TG/DC les rendent, `WebFormatter.edit_reasoning()`/`edit_tool_recap()` = **no-ops** (`del …; pass`). Jeté au bord web. | **ADOPT (haute valeur)** | Parité web ↔ TG/DC quasi-gratuite. Asterisk : tool-call full-fidelity (voir §4). |
| **Provider adapters / per-model typing** | Routing 100 % server-side Python (LiteLLM/llmCLI + GPU local + clipool Claude CLI). Client envoie `harness+model` string. | **REDUNDANT / SKIP** | Client ne choisit jamais un provider → narrowing compile-time sans surface. |
| **Isomorphic / client tools** | Tous les tools exécutés server-side + autorisés au hub Stage-7 (`agent_grants` deny-by-default, ACL NKey). | **REDUNDANT — ne PAS adopter (exécution)** | `.client()` **bypasse Stage-7** = régression sécu. Seul le *rendering* (ToolCallPart) a de la valeur. |
| **Approval / HITL** | Aucun gate de confirmation par-turn. | **BORROW-IDEA** | Axe ≠ grant matrix (policy vs « run THIS call ? »). Shape d'event + widget réutilisables ; la moitié serveur (resume) = à ré-implémenter en Python. |
| **Middleware (onChunk / contentGuard / otel)** | Pipeline de stages server-side = déjà « le middleware » ; Stage-7 = short-circuit. TraceContext/otel/Loki riches. | **REDUNDANT (mostly)** | À prendre : `contentGuard` → stage egress redaction (absent) ; hiérarchie de spans otel call→loop→tool. |
| **Devtools (timeline / fixture replay)** | Trace serveur forte, **zéro** introspection interaction côté dashboard. | **BORROW-IDEA** | Timeline run/thread + tool I/O + fixture replay (localStorage) utiles pour v2. |
| **Media / realtime voice** | voiceCLI = TTS/STT GPU local. Web `supports_audio=False`, egress audio **ACL-denied by design**. | **SKIP / NO FIT** | Adapters TanStack = cloud direct, bypasseraient voiceCLI + authz. Pattern `useGeneration` = référence seulement. |
| **MCP wiring** | — (orchestration serveur ⇒ Python). | **SKIP** | Server-side only → orphelin comme les server-tools TS. |
| **Repo pattern (core headless + wrappers + protocol pkg)** | Axe « thin adapters over shared contracts » déjà en place. | **BORROW-IDEA** | Voler la structure, pas l'engine. |

---

## 4. Recommandation — le plus petit move sensé (dashboard-v2)

Deux changements ; le **1er est load-bearing et à faire indépendamment du choix de client**.

**Step 1 — SERVEUR (Python).** `chat_routes.py` `event_gen()` + `WebFormatter` sérialisent en SSE AG-UI (`RUN_STARTED` / `TEXT_MESSAGE_START/CONTENT/END` / `RUN_FINISHED` / `RUN_ERROR`) au lieu de `{delta/done/error/ping}`. Un-no-op `edit_reasoning` → `REASONING_*`, `edit_tool_recap` → `TOOL_CALL_*`.
- **Dep** : `ag-ui-protocol` (Pydantic + `EventEncoder`) — cheap (roxabi-contracts déjà Pydantic) ; ou hand-roll ~8 shapes.
- **Ligne la plus piégeuse** : cumulatif→incrémental. `WebFormatter._buffer = text` (buffer complet) mais un `delta` AG-UI doit être **incrémental** (`text[len(last_sent):]`). Pin contre la valeur on-wire avant de coder.
- **Transport** : keepalive `ping` → commentaire SSE `: ping\n\n` (pas un event AG-UI) ; sentinelle `data: [DONE]\n\n` (TanStack l'ajoute, l'encoder AG-UI non — vérifier une fois puis lock).
- **Asterisk tool-call (vérifié)** : l'`OutboundEmitter` partagé **aplatit** `ToolCall*` en **recap strings** *avant* le formatter (WebFormatter ne reçoit jamais les events tool structurés) → la fidélité tool-call complète exige soit un changement du **stage emitter partagé** (touche TG/DC), soit l'adapter « AG-UI HTTP/SSE » différé abonné au flux RenderEvent brut. Ni l'un ni l'autre n'est core/domain, mais **ne pas sur-croire le « 2 fichiers, no stage changes »** pour les tools (vrai pour Text + Reasoning uniquement).
- **Effort** : small-medium, quelques jours. Text + Reasoning = vrai swap de sérialiseur edge-only.

**Step 2 — CLIENT (v2 only, greenfield).** Page chat avec `useChat` par-dessus un **`ConnectConnectionAdapter` custom** qui bridge le handshake 2-temps (`POST /api/chat` → `{session_id, stream_token}`, puis `GET /api/stream/{id}?token=`). Le `fetchServerSentEvents` par défaut suppose un unique POST `RunAgentInput` → ~1 fichier custom. On récupère `message.parts` / ThinkingPart / ToolCallPart / `isLoading` / `stop` gratis. Ne PAS adopter `clientTools`, provider typing, media. Laisser jobs/pipeline EventSources tranquilles.

**Risques :**
- Dep supplémentaire (`@tanstack/ai-react` + peer `@tanstack/ai`) pour **une** surface interne ; machinerie client-tool/approval = **opt-in** → dead weight en pur renderer, mais tree-shakeable et inoffensive.
- **AG-UI jeune** (`@ag-ui/core` 0.0.5x). Superset additif → base events Python valides, mais **vérifier une fois la sémantique terminale** (`[DONE]` vs `RUN_FINISHED`) contre le parser client, puis lock.
- **Discipline d'adoption (verdict PARTIAL)** : AG-UI **supporte** les tools client-exécutés → sûr **par discipline** (backend-tools only, jamais register `.client()`), **pas par le protocole**. Sinon un tool navigateur bypasse Stage-7.
- **#2262** (dashboard BFF unauth sur le tailnet) = **orthogonal** mais à fixer de toute façon. Le bridge préserve le `stream_token` → n'aggrave pas la posture. Précision : `POST /api/chat` lui-même est unauth ; le token est anti-hijack par-session, **pas** de l'auth utilisateur.
- Repro : proxy `/api → 127.0.0.1:8765` de v2 gated derrière `DASHBOARD_MOCK=0` (`dev:live`) ; `dev` par défaut sert `/api` depuis le dev-mock. **reachable ≠ protocol-compatible** tant que Step 1 non livré.

**Baseline do-nothing :** client chat v1 ≈ 40 lignes. Si le chat web reste une surface smoke jetable (`WEB_BOT_ID=smoke`, `stream_tokens` = mitigation interim #1992), raw EventSource est défendable et moins cher. v2 n'a pas de chat → « rien » = ne pas l'ajouter dans v2.

**Verdict :** **BORROW le protocole, ADOPT `useChat` en v2** comme renderer par-dessus un edge AG-UI. Fais **Step 1 d'abord** (c'est du serveur que tu veux même si le client reste hand-rolled), traite l'adoption TanStack comme un follow-on quasi-gratuit, pas comme le driver. Un `/dev` cycle borné (adapter web + une page v2). Ne PAS parier sur le framework complet.

---

## 5. Idées à voler même sans adopter la lib

- **Protocol-first boundary** — posséder le transport comme **contrat d'events typé versionné**, pas des dicts ad-hoc. L'edge web émet un vocabulaire explicite (ids, run lifecycle), fini le `{delta full-buffer}`.
- **AG-UI comme UN wire contract sur TOUS les adapters** — web/TG/DC partagent déjà `render_events` sur l'axe stage ; les projeter sur **un** vocabulaire AG-UI unique → chaque canal rend reasoning/tool à l'identique (le web est le seul à les jeter). Unifier le modèle d'events **sur l'axe primaire**, pas par plateforme.
- **Observabilité devtools/middleware** — timeline run/step client + tool I/O + **fixture replay** (localStorage, dev sans backend) ; la factory a une trace serveur forte mais **zéro** introspection d'interaction côté dashboard. Debug toggle par catégorie. `contentGuard` = pattern pour un stage egress redaction (absent). Hiérarchie de spans otel (call→loop→tool) = shape pour aligner TraceContext.
- **Per-model capability typing** — si v2 expose un jour un model-picker forwardant vers le router Python, copier le pattern generic-factory + capability-map. Sinon N/A.

---

## Appendice — vérification adversariale (8 claims load-bearing)

| # | Claim (abrégé) | Verdict |
|---|---|---|
| 1 | On-wire = event AG-UI (`StreamChunk = AGUIEvent`) + SDK Python officiel `ag-ui-protocol` ⇒ FastAPI SSE nourrit `useChat` sans serveur TS ni shim JS (contra Vercel `@ag-ui/vercel-ai-sdk`) | **SUPPORTED** |
| 2 | Factory a déjà back-porté les 4 familles AG-UI en interne (ADR-070) ; doc « AG-UI is not adopted as a wire format » + adapter HTTP/SSE planifié ⇒ émettre AG-UI au edge = serializer swap, pas nouveau domain model | **SUPPORTED** |
| 3 | Reasoning/tool events arrivent déjà au web adapter mais jetés par 2 no-ops (`edit_reasoning`/`edit_tool_recap`) ; TG/DC les rendent ⇒ web thinking/tool ≈ gratuit (pas de travail bus/core) | **SUPPORTED** |
| 4 | AG-UI ne modélise ni provider selection ni tool-execution ni authz ⇒ routing/CLI/Stage-7 restent Python, rien de tiré client-side | **PARTIAL** — AG-UI *modélise* les tools client-side (permis, pas mandaté) ; conclusion sécu tient **par discipline d'adoption**, pas par le protocole |
| 5 | dashboard-v2 greenfield, déjà TanStack, sans chat, sans dep `@tanstack/ai`, proxy `/api→8765` ⇒ adoption client purement additive | **SUPPORTED** (caveats : proxy gated `DASHBOARD_MOCK=0` ; mismatch protocole = moitié serveur, hors-scope client) |
| 6 | Mismatch 2-step (mint/verify token) vs single-POST RunAgentInput bridgeable par `ConnectConnectionAdapter` custom préservant le `stream_token` | **SUPPORTED** (« thin » généreux : l'adapter traduit aussi le vocabulaire SSE ad-hoc + diff full-buffer→incrémental) |
| 7 | Surface lourde (tools/approval/MCP/media/voice) redundant/misaligned ; seul le slice AG-UI-edge + useChat porte de la valeur et est séparable de l'engine | **SUPPORTED** (`@tanstack/ai-client` standalone, pas de dep obligatoire sur le core engine) |
| 8 | Pattern repo headless-core + thin-wrappers + protocol-pkg réel et worth-borrowing ; `@ag-ui/core` canonique (pas fork), sibling Python même spec | **SUPPORTED** |

**Sources** (primaires) : `tanstack.com/ai/latest/docs/*` (overview, connection-adapters, tools, comparison/vercel-ai-sdk, migration/ag-ui-compliance) · `github.com/TanStack/ai` (`packages/ai/package.json` → `@ag-ui/core ^0.0.52` ; `types.ts` → `StreamChunk = AGUIEvent`) · `tanstack.com/blog/ag-ui-compliance` · `docs.ag-ui.com` + `github.com/ag-ui-protocol/ag-ui` · `pypi.org/project/ag-ui-protocol` (`ag_ui.core`, `ag_ui.encoder.EventEncoder`) · in-repo : `src/factory/core/messaging/render_events.py`, `docs/architecture/llm-streaming.md:131-152`, ADR-070, `src/factory/adapters/web/{chat_routes,web_formatter,web_outbound}.py`, `src/factory/outbound/_emitter_run.py`, `apps/dashboard-v2/{package.json,vite.config.ts,src/router.tsx}`.
