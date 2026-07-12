# Phase 0 companion — schema detail + threat model

> Companion to [ADR-103](../../docs/architecture/adr/103-dashboard-auth-user-org-platform-link.mdx)
> and [goal](../goal/dashboard-auth-identity-org-goal.md). **Design only.**

---

## 1. Entity states

### Invite

| status | Meaning |
|--------|---------|
| `pending` | Token valid until `expires_at` |
| `accepted` | User created/activated; token dead |
| `revoked` | Admin cancelled before accept |
| `expired` | Past `expires_at` (or lazy-mark on use) |

### User

| status | Meaning |
|--------|---------|
| `active` | May login / use API keys |
| `disabled` | Authn fails; existing sessions revoked on check |

### API key

| field | Rule |
|-------|------|
| `key_hash` | Only hash at rest |
| `prefix` | e.g. `fak_ab12…` for UI |
| `revoked_at` | Non-null → reject |

### Platform link

- V1: **at most one** TG and one DC per dashboard user.
- Platform id globally unique → one dash user (UNIQUE).
- Unlink allowed; chat_ready becomes false until re-linked both.

---

## 2. Authorize matrix (control-plane)

| action (examples) | admin | owner | org member | outsider |
|-------------------|:-----:|:-----:|:----------:|:--------:|
| list own jobs | ✓ | ✓ | ✓ if org | ✗ |
| list peer private job | ✓ | ✗ | ✗ | ✗ |
| list org job | ✓ | ✓ | ✓ | ✗ |
| steer/cancel | ✓* | ✓ | ✓ if org | ✗ |
| agents.soul write | ✓ | policy TBD (admin V1) | policy TBD | ✗ |
| invite user | ✓ | ✗ | ✗ | ✗ |
| create org | ✓ | any active user | — | — |
| add org member | ✓ | org owner | ✗ | ✗ |

\* admin uses global role, not “ownership”.

V1 simplification allowed: **only admin** mutates agents/soul/users; members get read + jobs in own/org — document any widen in impl PR.

---

## 3. Threat model (STRIDE-lite)

| Threat | Mitigation |
|--------|------------|
| Anonymous BFF use | Fail-closed `require_principal`; no Tailnet trust |
| Spoof principal on NATS body | BFF stamps server-side; hub rejects empty; never trust client actor fields |
| Stolen web-adapter seed | Still need valid principal stamp; later optional dedicated NATS identity; seed rotation runbook |
| Stolen session cookie | HttpOnly Secure SameSite; session hash; expiry; logout revoke |
| Stolen API key | Hash at rest; show once; revoke; optional scopes |
| Invite token leak | Hash token; short TTL; single use |
| Privilege escalate member→admin | Only admin assigns global_role |
| Org IDOR | Membership check on every org_id write/read |
| Unlinked TG abuse | Refuse before agent pipeline |
| Password stuffing | Rate limit login (Block 13); strong hash |
| E2E left open in prod | Explicit flag only; never “empty secret = open” |
| Health auth break | Public liveness only |

### Trust boundaries

```text
Internet/Tailnet/LAN  →  BFF authn boundary (Principal)
web-adapter NKey      →  process may call hub subjects (ACL)
Principal on RPC      →  hub authz boundary
platform message      →  link boundary then ADR-090 grants
```

---

## 4. Pairing link (V1 mechanism — recommended)

1. Authenticated user opens Dashboard → “Link Telegram”.
2. Server mints short-lived `link_code` (hash stored, user_id bound).
3. User sends `/link <code>` to the factory bot (or deep link).
4. Adapter/hub validates code → writes `platform_links`.
5. Same for Discord.
6. UI shows chat_ready when both present.

**Not V1:** OAuth app install (can supersede without schema change).

---

## 5. Bootstrap sequence (runbook sketch)

1. Install creates session signing secret (Podman).
2. `factory setup` / migrate creates admin user (password from stdin or generated once).
3. Admin logs in console.
4. Admin invites users; creates orgs as needed.
5. Each user (incl. admin) links TG+DC before chat.
6. Admin grants agent USE to platform ids or via “grant human” helper (Block 10).

---

## 6. Open items — **locked GO 2026-07-12**

| # | Question | Decision (GO) |
|---|----------|---------------|
| 1 | Accept ADR-103 as **Accepted** vs stay Proposed until PR1? | Stay **Proposed** until first impl PR merges |
| 2 | V1 member may create orgs? | **Yes** (any active user) |
| 3 | V1 member mutates agents/soul? | **No** — admin only until product asks |
| 4 | Pairing `/link` vs OAuth first? | **Pairing code** |
| 5 | Grants issued on dash user vs platform ids? | Platform ids + multi-grant on “grant human” |

---

*Phase 0 companion — 2026-07-11 · GO 2026-07-12*
