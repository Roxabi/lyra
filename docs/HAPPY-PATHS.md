# Lyra — End-to-End Happy Paths

> Living document. Catalogs every happy-path scenario across the system.
> Last updated: 2026-03-17

---

## Overview

Lyra has **26 distinct happy paths** organized into 7 categories. Each scenario documents the trigger, processing pipeline, and expected output for one complete end-to-end flow.

| Category | Count | Scope |
|----------|-------|-------|
| Core Message Flows | 4 | Text in → text out, streaming, formatting, typing |
| Commands | 5 | Builtins, plugins, session commands, URL rewrite, workspace |
| Audio / Voice | 3 | STT inbound, TTS outbound, Discord voice channel |
| Session & Memory | 5 | Multi-turn, reset, eviction, recall, compaction |
| Routing & Resilience | 4 | Smart routing, rate limiting, backpressure, circuit breaker |
| Auth & Multi-Bot | 3 | Trust levels, multi-bot isolation, reply threading |
| Media & Observability | 2 | Attachments, turn logging |

---

## 1. Core Message Flows

### 1.1 Basic Text Message

**Trigger:** User sends a text message in Telegram or Discord.

**Flow:**

```
Adapter.on_message()
  → Authenticator.resolve(user_id) → TrustLevel
  → GuardChain.check(msg) → pass/reject
  → normalize to InboundMessage (user_id, scope_id, text, trust_level)
  → InboundBus.put(platform, msg)
    → staging queue (bounded 500)
      → Hub.run() consumes
        → rate limit check (HubRateLimiter: 20 msgs/user/60s)
        → MessagePipeline.process()
          → CommandParser — not a command
          → resolve_binding(msg) → (agent, pool_id)
          → PoolManager.get_or_create_pool(pool_id, agent_name)
          → pool._inbox.put(msg)
          → PoolProcessor.process(msg)
            → Agent.process(msg, pool)
              → build system_prompt (identity + memory)
              → LlmProvider.complete(pool_id, text, model_cfg, system_prompt)
                → SmartRouting → CircuitBreaker → Retry → Driver
              → LlmResult(result="...")
            → Response(content="...")
        → Hub.dispatch_response(msg, response)
          → OutboundDispatcher.enqueue(outbound)
            → Adapter.send(msg, outbound)
              → Telegram: bot.send_message(chat_id, text, reply_to)
              → Discord: message.reply(text) or channel.send(text)
            → capture reply_message_id
        → PoolObserver.log_turn() (fire-and-forget)
```

**Output:** Text reply in the conversation.

---

### 1.2 Streaming Text Response

**Trigger:** LLM provider yields content in chunks (streaming enabled).

**Flow:**

```
[same intake as 1.1 through Agent.process()]
  → LlmProvider.complete(stream=True) yields chunks
  → Hub.dispatch_streaming(msg, chunks_iterator)
    → voice modality? accumulate full → dispatch_response() once
    → text modality:
      → Adapter.send_streaming(msg, chunks, outbound)
        → send placeholder message, capture message_id
        → MessageDebouncer accumulates chunks (80ms window OR 1024 chars)
        → Telegram: bot.edit_message_text(chat_id, message_id, accumulated)
        → Discord: message.edit(content=accumulated)
        → repeat until stream complete
        → final chunk: is_final=True, await last edit
```

**Output:** Single message that updates in place as content streams.

---

### 1.3 Markdown Formatting

**Trigger:** Agent response contains markdown (`**bold**`, `` `code` ``, `[link](url)`).

**Flow:**

```
[same as 1.1 through dispatch_response()]
  → Adapter.send()
    → Telegram: telegramify_markdown.markdownify(text)
      → escapes MarkdownV2 special chars: _*[]()~`>#\+\-=|{}.!
      → preserves bold, italic, code, code blocks
      → bot.send_message(parse_mode="MarkdownV2")
    → Discord: sends as-is (native markdown support)
```

**Output:** Formatted text with proper rendering per platform.

---

### 1.4 Typing Indicator

**Trigger:** Message received, before response is ready.

**Flow:**

```
Adapter.on_message()
  → Telegram: _start_typing_task(chat_id)
    → loop: bot.send_chat_action("typing") every 5s
  → Discord: _discord_typing_worker(channel_id)
    → async with channel.typing() (refreshes every 8s)
  → [message processes through hub → agent → LLM]
  → Adapter.send()
    → _cancel_typing()
    → send actual response
```

**Output:** "typing…" indicator visible while processing, disappears on send.

---

## 2. Commands

### 2.1 Builtin Command

**Trigger:** User sends `/help`, `/clear`, `/config`, `/stop`, `/circuit`, `/routing`.

**Flow:**

```
[intake through MessagePipeline]
  → CommandParser.parse(msg.text)
    → extracts prefix="/", name="help", args=remainder
    → attaches CommandContext to InboundMessage
  → router.is_command(msg) → True
  → CommandRouter dispatches to builtin handler (builtin_commands.py):
    → /help    → list available commands
    → /clear   → reset pool history + backend session (see 4.2)
    → /config  → show/set runtime config (agent parameters)
    → /stop    → cancel current processing task
    → /circuit → show circuit breaker status (admin-only)
    → /routing → show smart routing decisions (admin-only)
  → or workspace handler (workspace_commands.py):
    → /workspace → list/switch workspaces
  → handler returns Response(content="...")
  → PipelineResult(action=COMMAND_HANDLED)
  → Hub.dispatch_response(msg, response)
```

**Output:** Command response (help text, config dump, status).

---

### 2.2 Plugin Command

**Trigger:** User sends `/echo hello world` (or any registered plugin command).

**Flow:**

```
[intake through CommandRouter]
  → Router checks enabled_plugins
    → CommandLoader loads plugin TOML: /echo → handler="cmd_echo"
    → dynamic import: handlers.cmd_echo(msg, pool, args=["hello", "world"])
  → plugin handler processes and returns Response
  → dispatch_response()
```

**Output:** Plugin-specific response.

---

### 2.3 Session Command

**Trigger:** User sends `/add`, `/explain`, or `/summarize` with a URL.

**Flow:**

```
[intake through CommandRouter]
  → Router dispatches to session command handler
    → scrape_url(url) → raw web content
    → isolated pool: pool_id="session:add" (no history pollution)
    → driver.complete(
        pool_id="session:add",
        text=web_content,
        system_prompt=ADD_SYSTEM_PROMPT
      )
    → LLM processes scrape in isolation
    → handler parses output (Title, Summary, Tags)
    → /add:       vault_add(title, summary, tags)
    → /explain:   returns explanation
    → /summarize: returns bullet points
  → Response(content="Saved to vault" or explanation text)
  → dispatch_response()
```

**Output:** Confirmation (for `/add`) or extracted content (for `/explain`, `/summarize`).

---

### 2.4 Bare URL Auto-Rewrite

**Trigger:** User sends a plain URL as the entire message (`https://example.com`).

**Flow:**

```
[intake through MessagePipeline]
  → regex match: ^https?://\S+$
  → rewrite msg.text → "/add https://example.com"
  → attach CommandContext for /add
  → dispatch to session command handler (flow 2.3)
```

**Output:** URL scraped and saved to vault silently.

---

### 2.5 Workspace Switch

**Trigger:** User sends `/folder ~/projects/foo` or `/workspace <name>` (CliPool agents only).

**Flow:**

```
[intake through CommandRouter → workspace_commands.py]
  → /folder or /workspace handler
    → parse path/name argument: ~/projects/foo or workspace name
    → expanduser() → /home/user/projects/foo
    → pool._switch_workspace_fn(path)
      → CliPool._cwd_overrides[pool_id] = path
      → kill current Claude subprocess
      → next message respawns subprocess with new cwd
  → Response(content="Workspace switched to /home/user/projects/foo")
```

**Output:** Agent now operates in the new directory.

---

## 3. Audio / Voice

### 3.1 Voice Input (STT)

**Trigger:** User sends a voice message or audio file.

**Flow:**

```
Adapter receives voice message
  → Telegram: getFile → download to temp
  → Discord: CDN URL → download to temp
  → normalize to InboundAudio(audio_bytes, mime_type, scope_id, user_id)
  → InboundAudioBus.put(platform, audio)
    → Hub._audio_loop() consumes
      → STTService.transcribe(path)
        → voicecli library (faster-whisper)
        → Whisper model (large-v3-turbo, ~3GB VRAM)
        → loads personal vocab from ~/.voicecli/voicecli.vocab
        → TranscriptionResult(text="transcribed text", language="en")
      → on failure/silence: dispatch stt_error → return
      → on success:
        → create InboundMessage:
          → text = "🎤 [Voice] transcribed text"
          → modality = "voice"
          → language = "en"
        → enqueue to InboundBus staging queue
        → Hub.run() processes as text (flow 1.1)
```

**Output:** Transcribed text processed as normal message; response sent back.

---

### 3.2 Voice Output (TTS)

**Trigger:** User sends `/voice hello` or agent returns audio.

**Flow:**

```
/voice command handler:
  → rewrite msg: modality="voice", prepend "[Voice message requested]"
  → Agent.process() → provider.complete() → LlmResult
  → TTSService.synthesize(response_text, language, voice)
    → voicecli library (Qwen TTS)
    → chunk text into safe sizes
    → synthesize each chunk → WAV
    → merge WAV chunks → single WAV
    → compute duration_ms from WAV header
    → compute waveform_b64 (256-byte amplitude array for Discord voice bubble)
    → convert WAV → OGG/Opus (48kHz mono, ffmpeg)
  → SynthesisResult(audio_bytes, mime_type="audio/ogg", duration_ms, waveform_b64)
  → Hub._synthesize_and_dispatch_audio(msg, response_text)
    → check user TTS prefs (voice/language overrides)
    → create OutboundAudio envelope
    → dispatch_audio(msg, audio)
      → Telegram: bot.send_voice(chat_id, ogg_bytes, duration=ms)
      → Discord: channel.send(file=File(ogg_bytes), flags=IS_VOICE_MESSAGE)
```

**Output:** Voice message from bot in conversation.

---

### 3.3 Discord Voice Channel (Live)

**Trigger:** `/voice hello` with user in a Discord voice channel.

**Flow:**

```
[same TTS synthesis as 3.2]
  → Hub.dispatch_voice_stream(msg, chunks)
    → VoiceSessionManager.join(guild_id, channel_id)
      → check deps: libopus, ffmpeg, discord.py[voice]
      → create VC connection
      → instantiate PCMQueueSource (thread-safe queue)
    → for each OGG chunk:
      → convert OGG → PCM (16-bit 48kHz)
      → write 3840-byte frames (20ms) to queue
    → discord.VoiceClient.play(PCMQueueSource)
      → spawns thread: source.read() in loop
      → sends to Discord WebRTC stream
    → on final chunk: source.push_eof()
      → VoiceClient stops playback
    → disconnect from voice channel
```

**Output:** Bot speaks in Discord voice channel in real-time.

---

## 4. Session & Memory

### 4.1 Multi-Turn Conversation

**Trigger:** User sends multiple messages in the same scope.

**Flow:**

```
Message 1:
  → create_pool() → session_id=UUID, message_count=0
  → append to pool.history + pool.sdk_history
  → LLM sees: [system_prompt, user_msg_1]
  → Response 1 → append to sdk_history (assistant turn)
  → TurnStore: fire-and-forget log to turns.db

Message 2:
  → append to history + sdk_history
  → LLM sees: [system_prompt, user_msg_1, asst_resp_1, user_msg_2]
  → Response 2 → append

Message N:
  → same pattern, full history visible to LLM
  → L0 compaction triggers if sdk_history > 160k tokens (see 4.5)
```

**Output:** Continuous conversation with full context.

---

### 4.2 Session Reset

**Trigger:** User sends `/clear`.

**Flow:**

```
CommandRouter → builtin /clear handler
  → pool.reset_session()
    → pool.history = []
    → pool.sdk_history = deque()
    → pool.session_id = str(uuid.uuid4())  # fresh UUID
    → call _session_reset_fn (injected by agent)
      → CliPool: SIGTERM to Claude subprocess, respawn on next message
      → AnthropicSdkDriver: no-op (stateless API)
  → Response(content="Session cleared")
```

**Output:** History cleared; next message starts a fresh session.

---

### 4.3 Pool Eviction (TTL)

**Trigger:** Pool idle for >1 hour (configurable `pool_ttl`).

**Flow:**

```
Hub._evict_stale_pools() (runs periodically, throttled: once per pool_ttl/10)
  → for each pool in hub.pools:
    → if pool.is_idle AND (now - pool._last_active) > pool_ttl:
      → agent.flush_session(pool, reason="idle")
        → writes L3 snapshot to roxabi-vault
        → background extraction: NER, concept/preference extraction
      → remove pool from hub.pools
```

**Output:** Memory persisted; pool freed from memory.

---

### 4.4 Cross-Session Memory Recall

**Trigger:** Message arrives from a known user with memory wired to the agent.

**Flow:**

```
Agent.build_system_prompt(msg, pool)
  → MemoryManager.recall(user_id, namespace, first_msg, token_budget=10k)
    → query roxabi-vault (AsyncMemoryDB):
      → L3 semantic DB (SQLite + fastembed + sqlite-vec)
      → BM25 search on user preferences/concepts
      → cosine similarity search on embeddings
    → returns [MEMORY] block: recent sessions, preferences, learned facts
  → system prompt assembled:
    → [SYSTEM] base prompt
    → [MEMORY] cross-session context
    → [IDENTITY] agent persona
    → [PREFERENCES] user preferences
    → current conversation history
  → LLM sees enriched context across sessions
```

**Output:** Agent remembers user context from past conversations.

---

### 4.5 L0 Compaction

**Trigger:** `sdk_history` exceeds ~160k tokens.

**Flow:**

```
Agent.process() → before LLM call:
  → check token count of sdk_history
  → if > 160k tokens:
    → summarize old turns into condensed summary
    → trim sdk_history to last 10 turns
    → prepend summary as system context
  → LLM sees: [system_prompt, [summary of old turns], ..., last 10 turns, new message]
```

**Output:** Context stays within model limits while preserving key information.

---

## 5. Routing & Resilience

### 5.1 Smart Routing (Complexity-Based Model Selection)

**Trigger:** Any inbound message.

**Flow:**

```
SmartRoutingDecorator.complete()
  → classify message complexity:
    → TRIVIAL:  greetings / very short (≤3 words + greeting pattern) → cheapest model
    → SIMPLE:   short factual (3–20 words) → lightweight model
    → MODERATE: medium (20–100 words) OR explanation keywords → mid-tier model
    → COMPLEX:  long (>100 words) OR code/analysis keywords → most capable model
  → look up routing_table[complexity] → model_id
  → override LlmProvider default model if mapping exists
  → decorator stack: SmartRouting → CircuitBreaker → Retry → Driver
  → driver executes with selected model
```

**Output:** Message routed to the appropriate model for its complexity.

---

### 5.2 Rate Limiting

**Trigger:** User sends >20 messages within 60 seconds.

**Flow:**

```
Hub._is_rate_limited(msg)
  → key = (platform, bot_id, user_id)
  → maintains deque[timestamp] per user
  → evict timestamps outside 60s sliding window
  → if deque.len >= 20: rate limited
    → MessagePipeline returns PipelineResult(action=DROP)
    → Hub sends error response: "Rate limited"
    → message dropped
```

**Output:** Excess messages dropped; user notified.

---

### 5.3 Bus Backpressure

**Trigger:** InboundBus staging queue full (maxsize=500).

**Flow:**

```
Adapter enqueues to per-platform queue (maxsize=100)
  → if full: raises asyncio.QueueFull
    → adapter catches → sends ack: "Message received, ~Xs wait"
    → blocking await bus.put(msg) until staging queue has space
  → when staging queue drains: put() completes
  → Hub processes queued messages normally
```

**Output:** User notified of queue backup; message eventually processes.

---

### 5.4 Circuit Breaker

**Trigger:** 5+ consecutive LLM call failures.

**Flow:**

```
LlmProvider.complete() → CircuitBreaker wrapper
  → state machine:
    CLOSED (normal):
      → calls pass through
      → record_success() / record_failure()
      → after 5 failures → transition to OPEN

    OPEN (fail-fast):
      → calls rejected immediately with CircuitOpenError
      → Hub._circuit_breaker_drop(msg) → "Service temporarily unavailable"
      → after timeout (60s) → transition to HALF_OPEN

    HALF_OPEN (probing):
      → next call tested
      → success → CLOSED (recovery)
      → failure → OPEN (still broken)
```

**Output:** Graceful degradation; fast failure instead of timeouts.

---

## 6. Auth & Multi-Bot

### 6.1 Auth Check

**Trigger:** Every inbound message.

**Flow:**

```
Adapter.normalize()
  → Authenticator.resolve(user_id, platform, bot_id)
    → check admin.user_ids (cross-bot admins) → OWNER
    → check [auth.telegram_bots[bot_id]].owner_users → OWNER
    → check blocked list → BLOCKED
    → fallback → default trust level (PUBLIC)
  → GuardChain.check(msg) → Rejection | None
  → InboundMessage.trust_level = TrustLevel enum
  → downstream: CommandRouter checks trust_level for admin-only commands
    → /circuit, /routing, /config require OWNER or TRUSTED
```

**Output:** Message processed with appropriate authorization level.

---

### 6.2 Multi-Bot Same Channel

**Trigger:** Two bots configured in the same Discord guild/channel.

**Flow:**

```
Hub.register_adapter(Platform.DISCORD, bot_id="bot1", adapter1)
Hub.register_adapter(Platform.DISCORD, bot_id="bot2", adapter2)

Register bindings:
  → (discord, bot1, channel:100) → agent:lyra, pool:discord:bot1:channel:100
  → (discord, bot2, channel:100) → agent:other, pool:discord:bot2:channel:100

Both bots receive same message in channel:
  → each adapter normalizes independently
  → each routes to its own pool (isolated history)
  → each responds via OutboundDispatcher keyed by (platform, bot_id)

Thread ownership (cross-bot silence):
  → bot1 creates thread → adds thread_id to bot1._owned_threads
  → bot2 receives message in that thread
  → bot2 checks: thread_id not in _owned_threads AND not @mentioned → drop
  → prevents duplicate responses
```

**Output:** Each bot maintains separate conversations; no cross-talk.

---

### 6.3 Reply Threading

**Trigger:** User replies to a bot message (Telegram/Discord native reply).

**Flow:**

```
Adapter extracts reply_to_message_id from platform message
  → MessageIndex.resolve(pool_id, reply_to_id)
    → PK lookup in message_index table (message_index.db)
    → return session_id for that (pool_id, platform_msg_id)
  → pool.resume_session(session_id)
  → preserves context of the replied-to message
```

**Output:** Reply correctly threaded to the related conversation.

---

## 7. Media & Observability

### 7.1 Attachments

**Trigger:** User sends an image, video, or file attachment.

**Flow:**

```
Adapter extracts attachments:
  → Telegram: photo[] → file_id, document → file_id, video → file_id
  → Discord: message.attachments[] → CDN URL
  → create Attachment objects:
    → type: "image" | "video" | "document" | "file"
    → url_or_path_or_bytes: "tg:file_id:..." or CDN URL
    → mime_type: "image/jpeg", "video/mp4", etc.
  → normalize to InboundMessage.attachments: list[Attachment]
  → Agent sees attachments (vision-capable models)

Outbound (agent returns attachment):
  → OutboundAttachment(data=bytes, type, mime_type)
  → Hub.dispatch_attachment(msg, attachment)
    → Adapter.render_attachment():
      → Telegram: send_photo / send_document / send_video
      → Discord: embed image inline / send as attachment
```

**Output:** Attachments displayed in conversation.

---

### 7.2 Turn Logging

**Trigger:** Every processed message (after response is generated).

**Flow:**

```
After response generated:
  → TurnStore.log_turn(pool_id, session_id, "user", user_text, inbound.id)
  → TurnStore.log_turn(pool_id, session_id, "assistant", response_text, outbound_id)
  → fire-and-forget: asyncio.create_task() — never blocks message processing
  → writes to ~/.lyra/turns.db (SQLite):
    → table: conversation_turns
    → columns: pool_id, session_id, role, content, user_id,
               inbound_message_id, reply_message_id, timestamp
  → query interface: get_session_turns(), get_pool_turns(), get_user_turns()
```

**Output:** Full audit trail of all conversation turns.

---

## Quick Reference

| # | Scenario | Key Components | Section |
|---|----------|---------------|---------|
| 1 | Basic text message | Adapter → Bus → Hub → Pool → Agent → LLM → Dispatch | 1.1 |
| 2 | Streaming response | Hub.dispatch_streaming → MessageDebouncer → edit_message | 1.2 |
| 3 | Markdown formatting | telegramify_markdown / Discord native | 1.3 |
| 4 | Typing indicator | send_chat_action loop / channel.typing() | 1.4 |
| 5 | Builtin command | CommandParser → CommandRouter → handler | 2.1 |
| 6 | Plugin command | CommandLoader → dynamic import → handler | 2.2 |
| 7 | Session command | Isolated pool → LLM → vault | 2.3 |
| 8 | Bare URL rewrite | Regex → `/add` rewrite → session handler | 2.4 |
| 9 | Workspace switch | `/folder` → CliPool cwd override → respawn | 2.5 |
| 10 | Voice input (STT) | Whisper transcribe → inject as text | 3.1 |
| 11 | Voice output (TTS) | Qwen TTS → WAV → OGG/Opus → send_voice | 3.2 |
| 12 | Discord voice channel | PCMQueueSource → VoiceClient → WebRTC | 3.3 |
| 13 | Multi-turn conversation | Pool sdk_history accumulation | 4.1 |
| 14 | Session reset | `/clear` → pool.reset_session() | 4.2 |
| 15 | Pool eviction | TTL > 1h → flush_session → L3 snapshot | 4.3 |
| 16 | Cross-session recall | MemoryManager.recall() → L3 semantic DB | 4.4 |
| 17 | L0 compaction | >160k tokens → summarize + trim | 4.5 |
| 18 | Smart routing | ComplexityClassifier → model_id → driver | 5.1 |
| 19 | Rate limiting | Sliding window 20/60s → DROP | 5.2 |
| 20 | Bus backpressure | Queue full → ack → blocking await | 5.3 |
| 21 | Circuit breaker | CLOSED → OPEN → HALF_OPEN → CLOSED | 5.4 |
| 22 | Auth check | Authenticator + GuardChain → TrustLevel enum | 6.1 |
| 23 | Multi-bot same channel | Per-bot pools + thread ownership | 6.2 |
| 24 | Reply threading | MessageIndex → message_index.db PK lookup | 6.3 |
| 25 | Attachments | Adapter extract → Agent vision → render | 7.1 |
| 26 | Turn logging | TurnStore fire-and-forget → turns.db | 7.2 |
