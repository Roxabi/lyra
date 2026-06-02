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

### Solution — C3 pattern (current)

Adapters do transport-level auth only (Telegram HMAC webhook secret, Discord gateway token). They always forward messages with `trust=PUBLIC` to NATS. Trust resolution is performed Hub-side by the Authenticator at middleware stages 2–3 (`ResolveTrustMiddleware` → `TrustGuardMiddleware`). BLOCKED users are dropped at the Hub before reaching the Bus or any agent.

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

> **Pre-C3 historical (superseded):** Before containerization, adapters resolved trust themselves and dropped BLOCKED messages before calling `normalize()`. This pattern is no longer used — adapters are untrusted normalizers that always send `PUBLIC`.

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

- [x] `Authenticator` (identity resolver) in `src/factory/core/auth/authenticator.py`
- [x] `GuardChain` (composable guard pipeline) in `src/factory/core/auth/guard.py`
- [x] `TrustLevel` enum in `src/factory/core/auth/trust.py`
- [x] Config-driven trust_map (TOML), parsed in src/lyra/core/auth.py
- [x] Integrated in TelegramAdapter + DiscordAdapter
- [x] CLIAdapter (trust = OWNER by default)
- [x] Rejection logging

> **Refactored in #313/#314**: The original monolithic AuthMiddleware was split into `Authenticator` (resolves user identity → TrustLevel) and `GuardChain` (runs composable guards sequentially, returning the first Rejection or None).

### Admin access

`owner_users` in `[auth.telegram]` / `[auth.discord]` are automatically added to the admin set at startup — no need to duplicate IDs in `[admin].user_ids`. Extra non-owner admins can be added there explicitly.

Module-level registry: lyra.core.admin — `is_admin(user_id)` / `set_admin_user_ids()` / `get_admin_user_ids()`. Plugins use `is_admin()` to gate admin-only commands without needing access to the config layer.

---

## #routing — RoutingContext + Adapter outbound verification — ✅ Shipped (#152)

Security invariant: adapters must verify `channel` and `bot_id` from `RoutingContext` before sending any response — a response may never be delivered by the wrong bot or to the wrong channel.

→ See `messaging.md` (RoutingContext) for the full dataclass definition, populate-at-intake pattern, and outbound verification code.

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

→ See `workers-tooling.md` (Model selection) for ComplexityEstimator and `COMPLEXITY_TO_MODEL` mapping. Model selection is a worker routing concern, not a security concern.

### Implementation status

`CommandParser` is shipped and wired into `middleware_pool.py` and Discord voice commands. The ComplexityEstimator / SmartRoutingDecorator exists in code but is disabled: `smart_routing.enabled=true` is rejected by the validator and `create` wizard. Model selection is fixed per agent config. The `COMPLEXITY_TO_MODEL` routing table below is therefore not active.

- [x] `CommandParser` + `CommandContext` — `src/factory/core/commands/command_parser.py`
- [x] Command routing in `CommandRouter`
- [ ] ComplexityEstimator with configurable signals — code exists, wiring disabled
- [ ] `COMPLEXITY_TO_MODEL` mapping in config — not active
- [ ] Dynamic upgrade mid-generation — not implemented

---

## #memory-isolation — Isolation + Metadata

### Problem

Without a strict partition by `user_id`, a bug or malformed query could return memories belonging to a different user.

### Security invariant

Every memory query at every level (L0–L4) must include `user_id` as an explicit filter. Never run a global query without a `user_id` filter. Even for stats, aggregate per user.

→ See `storage.md` for the full L0–L4 taxonomy, MemoryEntry schema, SQL isolation rule, counter-update code, and implementation checklist.

---

## #nats-infra — Brokered transport security

> Container-split (C3) introduced NATS as the inter-process bus. These 5 ADRs harden it: identity, scope, audit, ACL derivation, defensive provisioning.

### Nkey identity provisioning

`auth.conf` is a pure function of two inputs: the `IDENTITIES` manifest (constants in `gen-nkeys.sh`) and the seed directory on disk. A `--regen-authconf` mode re-renders the full file without rotating existing seeds — non-destructively closing drift caused by new identities added since the last generation. Missing seeds are auto-created; no identity in the manifest may be silently skipped. Each supervisor program must reference its own named seed file and fail fast if absent — the old `.env` fallback to `hub.seed` (which caused adapters to silently authenticate as hub) is classified as a misconfiguration, not a feature. A `lyra ops verify` command detects gaps between the manifest, disk seeds, and live `auth.conf` before harm occurs. → ADR-046

### Per-identity NATS inbox prefix

Every NATS identity must connect with `inbox_prefix="_INBOX.<identity-name>"`. This scopes all ephemeral inboxes that identity creates to `_INBOX.<identity>.>`, narrowing the ACL grant from the former bus-wide _INBOX.>. A leaked seed is therefore bounded to the compromised identity's own inbox namespace — it cannot be used to wiretap other identities' request-reply traffic. The fix is enforced at connect time via `roxabi_nats.nats_connect` with no changes to streaming logic. All current identities (hub, telegram-adapter, discord-adapter, tts-adapter, stt-adapter, voice-tts, voice-stt, image-worker) are covered. New identities added to `IDENTITIES` must supply `inbox_prefix` from their first connection. → ADR-051

### Security event audit

`CliPool` subprocess spawns (carrying `skip_permissions`, tools allowlist, model, PID, pool_id, agent_name) are audited via a port/adapter split that respects import layer boundaries. `AuditSink` is a `Protocol` defined in `factory.core.cli` — the port. `JetStreamAuditSink` in `factory.infrastructure.audit` is the concrete adapter; it publishes `SecurityEvent` (a `roxabi-contracts` Pydantic model) to the `LYRA_AUDIT` JetStream stream (`lyra.audit.>`, FILE storage, 90-day retention, 1 GiB cap). When JetStream is unavailable, the sink falls back to the lyra.security logger without crashing the runtime. Both `hub_standalone.py` and the unified `lyra start` bootstrap (`wiring_helpers.py:309`) wire the sink. → ADR-057

### ACL request/reply derivation

Responder inbox grants are no longer hand-written. A `request_reply_flows` section in `acl-matrix.json` declares each flow as `{ requester, responder, subject }`. The `load_matrix()` function in `gen-nkeys.sh` derives and injects the corresponding `_inbox.<requester>.>` publish grant for each responder automatically. Adding a new responder requires one JSON entry; the copy-paste pattern from ADR-062 Fix 2 — which had no enforcement and caused silent auth failures when any step was missed — is retired. A dedicated CI script (`scripts/check-request-reply-flows.sh`) validates identity existence, subject coverage, and that `--template-only` output contains the derived grants. → ADR-064

### Provisioning posture

`deploy/provision.sh` distinguishes missing `/etc/subuid` / `/etc/subgid` files (normal on a fresh install — silent skip) from unreadable-but-present files (permissions problem — emit `warn` and skip). This distinction applies to both `warn_subid_overlap` (advisory, no exit) and `assert_no_subid_overlap` (hard abort path). A whitelist guard rejects unexpected file paths in both functions, preventing misdirected `awk` scans in `curl | bash` execution contexts. The contract rule: advisory-path functions must still distinguish missing vs unreadable — they must not silently drop the entire check when a file exists but is unreadable. → ADR-069

### Key invariants

- No NATS identity may connect without an entry in the `IDENTITIES` manifest in `gen-nkeys.sh`.
- `auth.conf` is always regenerated from manifest + seed dir — it is never hand-edited, patched, or appended to incrementally.
- Every supervisor program references its own named seed file; missing seed → process exits non-zero (no silent fallback to another identity's seed).
- Every identity's `nats_connect` call supplies `inbox_prefix="_INBOX.<identity>"` — the bus-wide _INBOX.> grant is retired for all roles.
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
