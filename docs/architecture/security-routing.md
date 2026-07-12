# factory — — Security, Routing & Memory Isolation

> Reference document. Last updated: 2026-07-01.
> **Status**: #auth chat (#151 ✅ ADR-090), #routing (#152 ✅), #commands ✅, #memory-isolation — partial.
> **Control-plane auth**: **target** in [ADR-103](adr/103-dashboard-auth-user-org-platform-link.mdx) (Proposed) — see § control-plane below. Live code still Tailnet + optional fail-open bearer until goal ships.

---

## Overview

4 domains that together ensure an authorized user receives the correct response, from the correct agent, on the correct channel, with isolated memory.

```
[Channel] → ResolveIdentityMiddleware   (ban list + admin flag)
          → AuthorizeAgentMiddleware    (agent_grants SSoT)
          → CommandParser               (what action?)
          → Bus → Router                (which agent / pool?)
                  → ComplexityEstimator → LLMConfig   (which model?)
                  → Agent → MemoryManager (absolute user_id filter)
                          → RoutingContext (correct bot + correct channel)
          → Adapter (verifies routing before send)
```

---

## #auth — agent_grants SSoT (ADR-090) + ban list

### Problem

Without auth, any user can send a message that reaches the Bus and consumes resources (LLM tokens, memory, CPU).

### Solution — C3 + ADR-090 (current)

Adapters do transport-level auth only (Telegram HMAC webhook secret, Discord gateway token). They always forward messages with `trust=PUBLIC` to NATS. Hub-side enforcement is two stages:

1. **`ResolveIdentityMiddleware`** (stage 2) — re-resolves identity via `Authenticator`: drops `BLOCKED` users (ban list in `auth.db`), sets `is_admin` from `[admin].user_ids`.
2. **`AuthorizeAgentMiddleware`** (stage 6, after binding) — checks `agent_grants` for a `use` grant on the bound agent. Operators in `[admin].user_ids` bypass via `msg.is_admin`. Denied senders get a pull-model inline refusal (ADR-090 §5).

```python
class ResolveIdentityMiddleware:
    """Stage 2: ban list + admin flag (C3). Access is NOT gated here."""

    async def __call__(self, msg, ctx, nxt):
        msg = ctx.hub._resolve_message_trust(msg)  # Authenticator.resolve()
        if msg.trust_level == TrustLevel.BLOCKED:
            return DROP
        return await nxt(msg, ctx)

class AuthorizeAgentMiddleware:
    """Stage 6: agent_grants is the sole inbound access SSoT (ADR-090)."""

    async def __call__(self, msg, ctx, nxt):
        if msg.is_admin or authorizer.authorize(...).allowed:
            return await nxt(msg, ctx)
        return COMMAND_HANDLED  # pull refusal
```

> **Historical (superseded):** Bot-centric `owner_users` / `trusted_users` / `default_trust` on `BotRow` and the `ResolveTrustMiddleware` + `TrustGuardMiddleware` pair were removed in #2114. `TrustLevel.OWNER` / `TRUSTED` no longer gate inbound chat — only `BLOCKED` (ban) and `agent_grants` matter.

### Config

```toml
# config.toml (gitignored — copy from config.toml.example)
[admin]
user_ids = ["tg:user:7377831990"]   # global operators — bypass agent grant + admin commands

# Grant access per agent (CLI or pairing /join):
#   factory agent grant lyra_default --user tg:user:7377831990
```

Bot transport config lives in `BotStore` (no auth fields). Pairing `/join` writes agent-scoped `use` grants via `AgentGrantStore`.

### Implementation — ✅ Shipped (#151, ADR-090 #1980/#2114)

- [x] `Authenticator` (ban-only + admin resolution) in `src/factory/core/auth/authenticator.py`
- [x] `ResolveIdentityMiddleware` (hub-side BLOCKED drop, C3) in `middleware_guards.py`
- [x] `AuthorizeAgentMiddleware` (agent_grants enforcement) in `middleware_authz.py`
- [x] `AgentGrantStore` / operator CLI (`factory agent grant|revoke|auth list`)
- [x] Integrated in TelegramAdapter + DiscordAdapter (forward `PUBLIC`, hub authoritative)
- [x] CLIAdapter (trust = OWNER by default)

> The dashboard HTTP BFF (`/api/bff/*`) is a **separate control plane** from the NATS inbound
> chat pipeline above. **Live (until ADR-103 ships):** optional shared bearer fail-open +
> Tailnet bind — see `src/factory/dashboard/auth.py`. **Target:** § control-plane below.

### Admin access (chat plane — live)

`[admin].user_ids` sets the global operator set at startup. These users get `is_admin=True` on every resolved identity and bypass `AuthorizeAgentMiddleware` until explicitly narrowed in a follow-up ADR.

Admin resolution lives in `Authenticator.resolve()` (`src/factory/core/auth/authenticator.py`) using `[admin].user_ids` from config. Plugins gate privileged commands via `msg.is_admin` on the inbound message (set by `ResolveIdentityMiddleware`).

---

## #control-plane — dashboard auth, users, orgs, platform link (target — ADR-103)

> **Status:** Proposed design ([ADR-103](adr/103-dashboard-auth-user-org-platform-link.mdx)).
> Execution: [`artifacts/goal/dashboard-auth-identity-org-goal.md`](../../artifacts/goal/dashboard-auth-identity-org-goal.md).
> This section is **target truth**; do not assume shipped until the goal blocks land.

### Problem

Control-plane BFF and `factory.dashboard.*` hub RPC must not rely on network perimeter
(Tailnet) or an anonymous process identity (`web-adapter` NKey alone). Multi-user product
needs invite-only accounts, organizations, API keys, and TG/DC only after dual platform link.

### Solution — Python authn in dashboard process + hub authz

| Layer | Responsibility |
|-------|----------------|
| **BFF (authn)** | Session cookie + API keys → `Principal(user, roles, orgs)`; invite accept; link pairing |
| **Hub `_wrap` (gate)** | Reject missing principal; `authorize(action, resource)`; handlers business-only |
| **Workers** | No user/org checks (actuators) |
| **TG/DC adapters** | Thin: resolve platform id → `platform_links`; unlinked → refuse chat turn |

**Rejected for V1:** Better Auth TS sidecar; Tailnet-as-auth; “ops” as identity kind;
default organization; open signup.

### Visibility

| Principal | Control-plane resources |
|-----------|-------------------------|
| Global `admin` | All |
| Member | `owner_user_id = me` ∪ `org_id ∈ memberships` |
| Unauthenticated | None (except health + login/invite public routes) |

Org-tagged resources are visible to **all members** of that org. **No default org** at invite.

### Chat readiness

```text
chat_ready ⇔ user active ∧ linked(telegram) ∧ linked(discord)
```

Console session works **without** link. **Admin included:** must link to use TG/DC bots.
Inbound unlinked platform ids never reach agent authorize (onboarding refusal).

### Principal on the bus

- Dashboard RPC: BFF stamps principal server-side (security-bearing dashboard contracts —
  not global `ContractEnvelope`).
- Hub fails closed without principal.
- NKey = process ACL only.

### Relation to #auth (ADR-090)

Chat `agent_grants` USE matrix stays the SSoT for **agent access**. Platform link maps
`tg:user:` / `dc:user:` → dashboard user for product identity; it does **not** replace
the grant matrix. Planes stay separate (ADR-103).

### Implementation checklist (goal blocks — not live yet)

- [ ] User / invite / session / API key / org / platform_link stores + ports
- [ ] `require_principal` fail-closed on BFF
- [ ] Hub `_wrap` principal + resource authorize
- [ ] Jobs `launched_by` + optional `org_id`
- [ ] Dual platform link + inbound refuse if unlinked
- [ ] SPA login / invite / org / link UX
- [ ] Secrets-policy session key; HealthCmd still public

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

`auth.conf` is a pure function of two inputs: the `IDENTITIES` manifest (identities map in `deploy/nats/acl-matrix.json`) and the seed directory on disk. A `--regen-authconf` mode in `factory-acl genkeys` re-renders the full file without rotating existing seeds — non-destructively closing drift caused by new identities added since the last generation. Missing seeds are auto-created; no identity in the manifest may be silently skipped. Each supervisor program must reference its own named seed file and fail fast if absent — the old `.env` fallback to `hub.seed` (which caused adapters to silently authenticate as hub) is classified as a misconfiguration, not a feature. A `factory ops verify` command detects gaps between the manifest, disk seeds, and live `auth.conf` before harm occurs. → ADR-046

### Per-identity NATS inbox prefix

Every NATS identity must connect with `inbox_prefix="_inbox.<identity-name>"`. This scopes all ephemeral inboxes that identity creates to `_inbox.<identity>.>`, narrowing the ACL grant from the former bus-wide _INBOX.>. A leaked seed is therefore bounded to the compromised identity's own inbox namespace — it cannot be used to wiretap other identities' request-reply traffic. The fix is enforced at connect time via `roxabi_nats.nats_connect(identity_name=...)` with no changes to streaming logic. All current identities (19 total: hub, telegram-adapter, discord-adapter, tts-adapter, stt-adapter, voice-tts, voice-stt, image-worker, and others) are covered. New identities added to the `identities` map in `acl-matrix.json` must supply `inbox_prefix` from their first connection. → ADR-051

### Security event audit

`CliPool` subprocess spawns (carrying `skip_permissions`, tools allowlist, model, PID,
pool_id, agent_name) are audited via a port/adapter split that respects import layer
boundaries. `AuditSink` is a `Protocol` port in `factory.core.ports.audit_sink`;
`JetStreamAuditSink` in `factory.infrastructure.audit` is the concrete adapter. It publishes
`SecurityEvent` (a `roxabi-contracts` Pydantic model, no transport imports) to the
`FACTORY_AUDIT` JetStream stream (`factory.audit.>` subjects, FILE storage, 90-day retention,
1 GiB cap) under the `factory.audit.security` publish prefix. Emission is fire-and-forget
and audit failure never blocks the message pipeline: when JetStream is unavailable the sink
degrades to an in-process security logger instead of crashing the runtime. Both the
standalone hub bootstrap and the unified `factory start` bootstrap wire the sink. Boundary:
this stream covers domain/runtime security events only — operator deploy actions
(`install.sh`, converge, credential rotations) are audited by the separate three-channel
operator audit (ADR-093), not by `FACTORY_AUDIT`. → ADR-057

### ACL request/reply derivation

Responder inbox grants are no longer hand-written. A `request_reply_flows` section in `acl-matrix.json` declares each flow as `{ requester, responder, subject }`. `load_matrix()` (`scripts/_loader.py`) reads the flows and the generator (`factory-acl genkeys`, `scripts/gen_nkeys.py`) derives and injects the corresponding `_inbox.<requester>.>` publish grant for each responder automatically — the expansion is centralized in `scripts/_effective.py` (`effective_grants()`). Adding a new responder requires one JSON entry; the copy-paste pattern from ADR-062 Fix 2 — which had no enforcement and caused silent auth failures when any step was missed — is retired. A dedicated CI gate (`request_reply_flows`, `uv run factory-check-flows`) validates identity existence, subject coverage, and that `--template-only` output contains the derived grants. → ADR-064

### Provisioning posture

`deploy/provision.sh` distinguishes missing `/etc/subuid` / `/etc/subgid` files (normal on a fresh install — silent skip) from unreadable-but-present files (permissions problem — emit `warn` and skip). This distinction applies to both `warn_subid_overlap` (advisory, no exit) and `assert_no_subid_overlap` (hard abort path). A whitelist guard rejects unexpected file paths in both functions, preventing misdirected `awk` scans in `curl | bash` execution contexts. The contract rule: advisory-path functions must still distinguish missing vs unreadable — they must not silently drop the entire check when a file exists but is unreadable. → ADR-069

### Key invariants

- No NATS identity may connect without an entry in the `identities` map of `deploy/nats/acl-matrix.json`.
- `auth.conf` is always regenerated from manifest + seed dir — it is never hand-edited, patched, or appended to incrementally.
- Every supervisor program references its own named seed file; missing seed → process exits non-zero (no silent fallback to another identity's seed).
- Every identity's `nats_connect` call supplies `inbox_prefix="_inbox.<identity>"` (lowercase) — the bus-wide _INBOX.> wildcard grant is retired for all roles.
- Responder inbox grants are derived from `request_reply_flows` in `acl-matrix.json` — no hand-written `_inbox.<requester>.>` entries in identity publish lists.
- `CliPool` subprocess spawns are audited to `FACTORY_AUDIT` JetStream stream; NATS unavailability degrades to logger, not crash.
- Advisory provisioning checks (`warn_subid_overlap`) distinguish missing files (skip silently) from unreadable files (warn operator); they do not suppress the check without notice.

### See also

- NATS subject naming, KV readiness probe → `messaging.md`
- Cross-project NATS SDK (absorbs ACL inbox case ADR-062) → `contracts.md` (ADR-045)
- Quadlet credential-store (absorbs ADR-054) → `deployment.md` (ADR-055)

---

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 046 | NATS access artifacts (auth.conf + ACL grants) are derived, never hand-written | Accepted — amended 2026-07-01/2026-07-04 (shell → `factory-acl genkeys`, manifest location) |
| 051 | Per-identity NATS inbox prefix as a security invariant | Accepted — amended 2026-07-01 (uppercase → lowercase `_inbox.<identity>`) |
| 090 | Agent-Scoped Authorization Matrix (Bots Inherit) | Accepted |

---

## Priority table

| Domain | Priority | Size | Dependencies | Status |
|--------|----------|------|--------------|--------|
| `#auth` | P0 | S | — | ✅ Shipped |
| `#routing` | P0 | M | `#auth` | ✅ Shipped |
| `#commands` | P1 | M | `#routing` | Partial (CommandParser ✅, ComplexityEstimator disabled) |
| `#memory-isolation` | P1 | M (extend #83) | — | Partial (user_id partition active, full schema open) |
| `#nats-infra` | P0 | M | `#auth` | ✅ Shipped (NATS C3 pattern) |
