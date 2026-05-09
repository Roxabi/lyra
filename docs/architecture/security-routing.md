# Lyra — Security, Routing & Memory Isolation

> Reference document. Last updated: 2026-05-09.
> **Status**: #auth (#151 ✅), #routing (#152 ✅), #commands ✅ (CommandParser shipped), #memory-isolation — partially implemented (user_id partition active in prefs_store; full MemoryEntry metadata schema not yet applied).

---

## Overview

4 domains that together ensure an authorized user receives the correct response, from the correct agent, on the correct channel, with isolated memory.

```
[Channel] → Authenticator + GuardChain  (who may speak?)
          → CommandParser               (what action?)
          → Bus → Router                (which agent / pool?)
                  → ComplexityEstimator → LLMConfig   (which model?)
                  → Agent → MemoryManager (absolute user_id filter)
                          → RoutingContext (correct bot + correct channel)
          → Adapter (verifies routing before send)
```

---

## #auth — Authenticator + GuardChain + TrustLevel

### Problem

Without auth, any user can send a message that reaches the Bus and consumes resources (LLM tokens, memory, CPU).

### Solution

Auth at the Adapter level, **before** the Bus. The message is rejected at the source.

> **Updated (2026-05-07) — C3 pattern:** In the containerized deployment, adapters always forward messages with `trust=PUBLIC` and trust resolution is performed Hub-side by the Authenticator (middleware stage 2–3). The adapter still validates transport-level auth (Telegram HMAC, Discord gateway token) before publishing to NATS. See `docs/architecture/deployment.md` — *Security* section — for the current C3 model.

```python
class TrustLevel(Enum):
    OWNER   = "owner"    # full access, all commands
    TRUSTED = "trusted"  # normal access
    PUBLIC  = "public"   # limited access (if enabled)
    BLOCKED = "blocked"  # silently rejected

class Authenticator:
    """Identity resolver — maps user_id to TrustLevel."""

    def resolve(self, user_id: str | None) -> TrustLevel:
        if user_id is None:
            return TrustLevel.BLOCKED
        return self._store.check(user_id)  # checks owner, trusted, blocked lists

class GuardChain:
    """Runs guards sequentially, returning the first Rejection or None."""

    async def check(self, msg) -> Rejection | None:
        for guard in self._guards:
            if rejection := await guard.check(msg):
                return rejection
        return None
```

**Integration in each Adapter:**

```python
async def on_event(self, raw_event) -> Message | None:
    user_id = self.extract_user_id(raw_event)
    trust = self.authenticator.resolve(user_id)
    if trust == TrustLevel.BLOCKED:
        return None  # dropped — never reaches the Bus
    msg = self.normalize(raw_event)
    msg.trust_level = trust
    rejection = await self.guard_chain.check(msg)
    if rejection:
        return None  # guard rejected
    return msg
```

### Config

```toml
# config.toml (gitignored — copy from config.toml.example)
[auth.telegram]
owner_users   = [123456789]    # numeric — get from @userinfobot on Telegram
trusted_users = []
default       = "blocked"

[auth.discord]
owner_users   = [123456789012345678]   # numeric snowflake
trusted_roles = []                     # Discord role snowflake IDs
default       = "blocked"
```

At least one section must be present. A missing section logs a warning and disables that adapter — Lyra starts with the remaining adapter. Both missing → `SystemExit`.

### Implementation — ✅ Shipped (#151, refactored #313/#314)

- [x] `Authenticator` (identity resolver) in `src/lyra/core/authenticator.py`
- [x] `GuardChain` (composable guard pipeline) in `src/lyra/core/guard.py`
- [x] `TrustLevel` enum in `src/lyra/core/trust.py`
- [x] Config-driven trust_map (TOML), parsed in `src/lyra/core/auth.py`
- [x] Integrated in TelegramAdapter + DiscordAdapter
- [x] CLIAdapter (trust = OWNER by default)
- [x] Rejection logging

> **Refactored in #313/#314**: The original monolithic `AuthMiddleware` was split into `Authenticator` (resolves user identity → TrustLevel) and `GuardChain` (runs composable guards sequentially, returning the first Rejection or None).

### Admin access

`owner_users` in `[auth.telegram]` / `[auth.discord]` are automatically added to the admin set at startup — no need to duplicate IDs in `[admin].user_ids`. Extra non-owner admins can be added there explicitly.

Module-level registry: `lyra.core.admin` — `is_admin(user_id)` / `set_admin_user_ids()` / `get_admin_user_ids()`. Plugins use `is_admin()` to gate admin-only commands without needing access to the config layer.

---

## #routing — RoutingContext + Adapter outbound verification — ✅ Shipped (#152)

### Problem

Without a complete `RoutingContext` in the `Response`, the outbound Adapter does not know which bot, which chat, or which thread to send to — risking delivery to the wrong destination in a multi-bot or multi-channel setup.

### Solution

Every `Response` carries a complete `RoutingContext`, populated at `InboundMessage` creation time.

```python
class RoutingContext:
    channel: str            # "telegram" | "discord" | "cli"
    bot_id: str             # identifier of the bot that must reply
    chat_id: str            # Telegram chat_id / Discord guild+channel
    thread_id: str | None   # forum thread, Discord thread
    reply_to_message_id: str | None  # native Telegram/Discord threading
    user_id: str
    session_id: str
```

**Populated at intake (in `normalize()`):**

```python
def normalize(self, update: TelegramUpdate) -> Message:
    return Message(
        ...
        routing=RoutingContext(
            channel="telegram",
            bot_id=self.bot_id,
            chat_id=str(update.message.chat.id),
            thread_id=str(update.message.message_thread_id) if update.message.is_topic_message else None,
            reply_to_message_id=str(update.message.message_id),
            user_id=str(update.message.from_user.id),
            session_id=self.make_session_id(update),
        )
    )
```

**Verified at outbound (in the Adapter):**

```python
async def send(self, response: Response) -> None:
    ctx = response.routing
    assert ctx.channel == self.channel, f"Wrong channel: {ctx.channel}"
    assert ctx.bot_id == self.bot_id,   f"Wrong bot: {ctx.bot_id}"
    await self.bot.send_message(
        chat_id=ctx.chat_id,
        text=response.content,
        message_thread_id=ctx.thread_id,
        reply_to_message_id=ctx.reply_to_message_id,
    )
```

### Implementation — ✅ Shipped (#152)

- [x] `RoutingContext` dataclass in `src/lyra/core/message.py`
- [x] Population in TelegramAdapter + DiscordAdapter `normalize()`
- [x] Outbound verification (channel + bot_id) in each adapter
- [x] Propagation of RoutingContext from InboundMessage → Response

---

## #commands — CommandParser + ComplexityEstimator

### Problem

Without command parsing, `/imagine`, `!help`, `/config` are treated as raw text by the LLM — no routing to the right skills/agents, no model optimization.

### CommandParser

```python
PREFIXES = ['/', '!']

class CommandContext:
    prefix: str       # "/" or "!"
    name: str         # "imagine", "help", "config"
    args: str         # remainder of message after the name
    raw: str          # full original text

class CommandParser:
    def parse(self, text: str) -> CommandContext | None:
        for prefix in PREFIXES:
            if text.startswith(prefix):
                parts = text[1:].split(None, 1)
                return CommandContext(
                    prefix=prefix,
                    name=parts[0].lower(),
                    args=parts[1] if len(parts) > 1 else "",
                    raw=text,
                )
        return None
```

**Command routing table:**

```python
COMMAND_ROUTING = {
    "imagine": ("image_agent", "image_pool"),
    "config":  ("admin_agent", "admin_pool"),
    "help":    ("lyra",        "default_pool"),
    "voice":   ("lyra",        "voice_pool"),
}
```

### ComplexityEstimator

Model selection based on message complexity — avoids using a heavyweight model for "hello".

```python
class ComplexityLevel(Enum):
    LOW    = "low"     # Haiku / Qwen-fast
    MEDIUM = "medium"  # Sonnet
    HIGH   = "high"    # Opus / Qwen full

class ComplexityEstimator:
    def estimate(self, msg: Message) -> ComplexityLevel:
        signals = [
            len(msg.content) > 500,
            self._contains_code(msg.content),
            len(msg.attachments) > 0,
            msg.command and msg.command.name in HIGH_COMPLEXITY_CMDS,
            msg.session_turn_count > 10,
            self._contains_question_chain(msg.content),
        ]
        score = sum(signals)
        if score == 0:
            return ComplexityLevel.LOW
        if score <= 2:
            return ComplexityLevel.MEDIUM
        return ComplexityLevel.HIGH

COMPLEXITY_TO_MODEL = {
    ComplexityLevel.LOW:    LLMConfig(provider="anthropic", model="claude-haiku-4-5-20251001"),
    ComplexityLevel.MEDIUM: LLMConfig(provider="anthropic", model="claude-sonnet-4-6"),
    ComplexityLevel.HIGH:   LLMConfig(provider="anthropic", model="claude-opus-4-6"),
}
```

### Implementation status

`CommandParser` is shipped and wired into `middleware_pool.py` and Discord voice commands. The `ComplexityEstimator` / `SmartRoutingDecorator` exists in code but is disabled: `smart_routing.enabled=true` is rejected by the validator and `create` wizard. Model selection is fixed per agent config. The `COMPLEXITY_TO_MODEL` routing table below is therefore not active.

- [x] `CommandParser` + `CommandContext` — `src/lyra/core/commands/command_parser.py`
- [x] Command routing in `CommandRouter`
- [ ] `ComplexityEstimator` with configurable signals — code exists, wiring disabled
- [ ] `COMPLEXITY_TO_MODEL` mapping in config — not active
- [ ] Dynamic upgrade mid-generation — not implemented

---

## #memory-isolation — Isolation + Metadata

### Problem

Without a strict partition by `user_id`, a bug or malformed query could return memories belonging to a different user. Without metadata, housekeeping (purge, stats, audit) is impossible.

### Extended MemoryEntry schema

```python
class MemoryEntry:
    # --- Identity ---
    id: UUID
    user_id: str            # ← ABSOLUTE partition key, never omitted

    # --- Sessions ---
    session_id_created: str
    session_id_modified: str

    # --- Content ---
    level: MemoryLevel      # L1 → L5
    content: str
    embedding: bytes        # sqlite-vec (L4 only)
    tags: list[str]

    # --- Metadata ---
    created_at: datetime
    updated_at: datetime
    count_usage: int        # incremented on each retrieve
    count_edits: int        # incremented on each write/update
    confidence: float       # reliability score (0.0 → 1.0)
    ttl: datetime | None    # auto-expiry (L1/L2)
    source: str             # "user" | "agent" | "system"
```

### SQL isolation rule (non-negotiable)

```sql
-- Every memory query must include user_id:
SELECT * FROM memory
WHERE user_id = :user_id        -- absolute isolation
  AND level IN (3, 4)           -- requested scope
  AND (ttl IS NULL OR ttl > datetime('now'))
ORDER BY count_usage DESC, updated_at DESC
LIMIT 20;
```

**Never run a global query without a `user_id` filter.** Even for stats, aggregate per user.

### Storage by level

| Level | Isolation |
|-------|-----------|
| L1 Working | `dict` in memory, scoped by `pool_id` |
| L2 Session | Store keyed by `(user_id, session_id)` |
| L3 Episodic | `~/.lyra/memory/episodic/{user_id}/YYYY-MM-DD/` — user_id in path |
| L4 Semantic | SQLite, `WHERE user_id = ?` mandatory on all queries |
| L5 Procedural | Global (skills = agent capabilities, not user data) |

### Counter updates

```python
async def retrieve(self, user_id: str, query: str, level: MemoryLevel) -> list[MemoryEntry]:
    entries = await self._search(user_id, query, level)
    for entry in entries:
        await self._increment_usage(entry.id)  # count_usage + 1
    return entries

async def write(self, user_id: str, content: str, level: MemoryLevel, session_id: str) -> MemoryEntry:
    existing = await self._find_similar(user_id, content)
    if existing:
        existing.content = content
        existing.count_edits += 1
        existing.updated_at = datetime.utcnow()
        existing.session_id_modified = session_id
        await self._save(existing)
        return existing
    return await self._create(user_id, content, level, session_id)
```

### Implementation status

`user_id` partitioning is active in `prefs_store.py` (L4 queries use `WHERE user_id = ?`). The full `MemoryEntry` metadata schema (count_usage, count_edits, confidence, ttl, source) is not yet applied uniformly — this was tracked as an extension to #83.

- [x] `user_id` isolation enforced in `prefs_store.py` queries
- [x] L3 path structure uses `{user_id}/` directories (session_lifecycle.py)
- [ ] Full `MemoryEntry` metadata schema with all fields above
- [ ] `count_usage` + `count_edits` auto-increment
- [ ] TTL auto-purge for L1/L2
- [ ] Per-user stats endpoint (usage, size, last activity)

---

## #nats-infra — Brokered transport security

> Container-split (C3) introduced NATS as the inter-process bus. These 5 ADRs harden it: identity, scope, audit, ACL derivation, defensive provisioning.

### Nkey identity provisioning

`auth.conf` is a pure function of two inputs: the `IDENTITIES` manifest (constants in `gen-nkeys.sh`) and the seed directory on disk. A `--regen-authconf` mode re-renders the full file without rotating existing seeds — non-destructively closing drift caused by new identities added since the last generation. Missing seeds are auto-created; no identity in the manifest may be silently skipped. Each supervisor program must reference its own named seed file and fail fast if absent — the old `.env` fallback to `hub.seed` (which caused adapters to silently authenticate as hub) is classified as a misconfiguration, not a feature. A `lyra ops verify` command detects gaps between the manifest, disk seeds, and live `auth.conf` before harm occurs. → ADR-046

### Per-identity NATS inbox prefix

Every NATS identity must connect with `inbox_prefix="_INBOX.<identity-name>"`. This scopes all ephemeral inboxes that identity creates to `_INBOX.<identity>.>`, narrowing the ACL grant from the former bus-wide `_INBOX.>`. A leaked seed is therefore bounded to the compromised identity's own inbox namespace — it cannot be used to wiretap other identities' request-reply traffic. The fix is enforced at connect time via `roxabi_nats.nats_connect` with no changes to streaming logic. All current identities (hub, telegram-adapter, discord-adapter, tts-adapter, stt-adapter, voice-tts, voice-stt, image-worker) are covered. New identities added to `IDENTITIES` must supply `inbox_prefix` from their first connection. → ADR-051

### Security event audit

`CliPool` subprocess spawns (carrying `skip_permissions`, tools allowlist, model, PID, pool_id, agent_name) are audited via a port/adapter split that respects import layer boundaries. `AuditSink` is a `Protocol` defined in `lyra.core.cli` — the port. `JetStreamAuditSink` in `lyra.infrastructure.audit` is the concrete adapter; it publishes `SecurityEvent` (a `roxabi-contracts` Pydantic model) to the `LYRA_AUDIT` JetStream stream (`lyra.audit.>`, FILE storage, 90-day retention, 1 GiB cap). When JetStream is unavailable, the sink falls back to the `lyra.security` logger without crashing the runtime. Both `hub_standalone.py` and the unified `lyra start` bootstrap (`wiring_helpers.py:309`) wire the sink. → ADR-057

### ACL request/reply derivation

Responder inbox grants are no longer hand-written. A `request_reply_flows` section in `acl-matrix.json` declares each flow as `{ requester, responder, subject }`. The `load_matrix()` function in `gen-nkeys.sh` derives and injects the corresponding `_inbox.<requester>.>` publish grant for each responder automatically. Adding a new responder requires one JSON entry; the copy-paste pattern from ADR-062 Fix 2 — which had no enforcement and caused silent auth failures when any step was missed — is retired. A dedicated CI script (`scripts/check-request-reply-flows.sh`) validates identity existence, subject coverage, and that `--template-only` output contains the derived grants. → ADR-064

### Provisioning posture

`deploy/provision.sh` distinguishes missing `/etc/subuid` / `/etc/subgid` files (normal on a fresh install — silent skip) from unreadable-but-present files (permissions problem — emit `warn` and skip). This distinction applies to both `warn_subid_overlap` (advisory, no exit) and `assert_no_subid_overlap` (hard abort path). A whitelist guard rejects unexpected file paths in both functions, preventing misdirected `awk` scans in `curl | bash` execution contexts. The contract rule: advisory-path functions must still distinguish missing vs unreadable — they must not silently drop the entire check when a file exists but is unreadable. → ADR-069

### Key invariants

- No NATS identity may connect without an entry in the `IDENTITIES` manifest in `gen-nkeys.sh`.
- `auth.conf` is always regenerated from manifest + seed dir — it is never hand-edited, patched, or appended to incrementally.
- Every supervisor program references its own named seed file; missing seed → process exits non-zero (no silent fallback to another identity's seed).
- Every identity's `nats_connect` call supplies `inbox_prefix="_INBOX.<identity>"` — the bus-wide `_INBOX.>` grant is retired for all roles.
- Responder inbox grants are derived from `request_reply_flows` in `acl-matrix.json` — no hand-written `_inbox.<requester>.>` entries in identity publish lists.
- `CliPool` subprocess spawns are audited to `LYRA_AUDIT` JetStream stream; NATS unavailability degrades to logger, not crash.
- Advisory provisioning checks (`warn_subid_overlap`) distinguish missing files (skip silently) from unreadable files (warn operator); they do not suppress the check without notice.

### See also

- NATS subject naming, KV readiness probe → `messaging.md`
- Cross-project NATS SDK (absorbs ACL inbox case ADR-062) → `contracts.md` (ADR-045)
- Quadlet credential-store (absorbs ADR-054) → `deployment.md` (ADR-055)

---

## Priority table

| Domain | Priority | Size | Dependencies | Status |
|--------|----------|------|--------------|--------|
| `#auth` | P0 | S | — | ✅ Shipped |
| `#routing` | P0 | M | `#auth` | ✅ Shipped |
| `#commands` | P1 | M | `#routing` | Partial (CommandParser ✅, ComplexityEstimator disabled) |
| `#memory-isolation` | P1 | M (extend #83) | — | Partial (user_id partition active, full schema open) |
| `#nats-infra` | P0 | M | `#auth` | ✅ Shipped (NATS C3 pattern) |
