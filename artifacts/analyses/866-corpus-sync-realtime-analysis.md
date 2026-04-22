# Analysis: Corpus Sync Real-time Mechanism

**Issue:** #866
**Date:** 2026-04-22
**Status:** Approved

---

## Context

Phase 4 of corpus sync (#829) originally planned a cron/systemd timer for nightly refresh. Investigation revealed GitHub organization webhooks can provide real-time updates with zero API quota consumption.

---

## Options Evaluated

| Approach | Mechanism | Latency | Infra Required | Quota Impact |
|----------|-----------|---------|----------------|--------------|
| Cron/timer | Poll GraphQL on schedule | 1–24h | None | Bounded, predictable |
| Events API polling | `GET /orgs/{org}/events` + ETag | 30s–6h | None | Low (304 on no-change) |
| Org webhook | GitHub POST → endpoint | Near real-time | HTTPS endpoint, secret | Zero (push) |
| **Webhook + Cron** | Both layers | Real-time + fallback | HTTPS endpoint | Minimal (weekly cron only) |

---

## Recommended: Webhook + Scheduled Reconciliation

### Why Both?

```
Webhook alone:
  - Misses events during downtime
  - Misses events created before webhook existed

Cron alone:
  - Latency 1–24h
  - Re-fetches unchanged issues

Webhook + Cron:
  - Webhook → instant, zero quota
  - Cron → safety net, catches drift
  - Lower cron frequency → less quota burn
```

---

## Organization Webhook Details

### Scope

Single webhook at `Roxabi` org level covers all repositories:

- `Roxabi/lyra`
- `Roxabi/voiceCLI`
- `Roxabi/roxabi-plugins`
- `Roxabi/roxabi-forge`
- ... all current + future repos

### Events

| Event | Actions | Corpus Impact |
|-------|---------|---------------|
| `issues` | opened, closed, edited, labeled, unlabeled, milestoned | Upsert issue row + labels |
| `issue_dependencies` | blocked_by_added/removed, blocking_added/removed | Upsert edges |
| `sub_issues` | parent_issue_added/removed, sub_issue_added/removed | Upsert edges (hierarchy) |

### Setup

**Requirement:** Org owner permission

```bash
gh api -X POST /orgs/Roxabi/hooks \
  -f name=web \
  -f config[url]=https://<endpoint>/webhook/github \
  -f config[content_type]=json \
  -f config[secret]=<SECRET> \
  -f events='["issues","issue_dependencies","sub_issues"]' \
  -F active=true
```

### Security

- Validate `X-Hub-Signature-256` header using HMAC-SHA256 with shared secret
- Reject unauthenticated requests

---

## Implementation Scope

### Phase 4a — Webhook Receiver

**File:** `scripts/corpus/webhook.py` (or integrated into lyra if endpoint exists)

**Logic:**
1. Receive POST at `/webhook/github`
2. Validate signature
3. Parse event type
4. Extract issue key, repo, number, title, state, labels, edges
5. Upsert to `corpus.db` via existing `upsert_*` helpers
6. Return 200 OK

**Dependency:** Public HTTPS endpoint on `roxabituwer` (M₁)

Options if no public ingress:
- Cloudflare Tunnel (free, no port forwarding)
- Tailscale Funnel
- ngrok (dev only)

### Phase 4b — Scheduled Reconciliation

**Mechanism:** systemd timer (preferred on M₁) or cron

**Frequency:** Weekly (Sunday 03:00 UTC)

**Why weekly instead of daily:**
- Webhook covers 99% of changes
- Cron is safety net only
- Reduces GraphQL quota consumption

**Logic:**
1. Enumerate org repos
2. Per-repo sync with `since = last_synced_at`
3. Closed-hop pass for stubs
4. Log rate limit stats

---

## Quota Comparison

| Scenario | Monthly Cost (GraphQL points) |
|----------|-------------------------------|
| Daily cron only | ~30 syncs × ~50 points = 1,500 |
| Webhook + weekly cron | ~4 syncs × ~50 points = 200 |

**Savings:** ~87% reduction in API quota usage.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Webhook endpoint downtime | Cron reconciliation catches missed events |
| Endpoint not publicly accessible | Use Cloudflare Tunnel or Tailscale Funnel |
| Org owner perm required | User (Mickael) is org owner |
| Secret management | Store in `~/.lyra/webhook.secret` or env var |

---

## Decision

Adopt **webhook + scheduled reconciliation** for Phase 4.

Split into:
- **4a:** Webhook receiver implementation
- **4b:** systemd timer for weekly sync

---

## References

- [GitHub Webhook Events](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [Creating Webhooks](https://docs.github.com/en/webhooks/creating-webhooks)
- [GitHub Events API](https://docs.github.com/en/rest/activity/events)
