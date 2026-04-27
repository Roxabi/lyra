# Deploy: Smoke Test + Canary Rollout

## Problem this solves

The 2026-04-27 incident showed that a bad image deploy caused 3h15m of silent failure across all 4 channels simultaneously. Two guardrails would have limited the blast radius:

1. **Canary** — deploy to 1 channel first, verify, then promote to all
2. **Smoke test** — confirm the NATS reply path works before declaring the deploy successful

---

## Canary rollout procedure

Use `lyra-telegram` (aryl bot) as the canary unit — lowest user impact, fastest to verify.

**Step 1 — Deploy canary**

```bash
# Pull new image
podman pull ghcr.io/roxabi/lyra:staging

# Restart canary unit only
systemctl --user restart lyra-telegram.service
```

**Step 2 — Smoke test the canary** (see below — run immediately after restart)

**Step 3 — Hold 5 minutes**

Watch logs for errors:

```bash
journalctl --user -u lyra-telegram -f
```

If the smoke test passed and no errors appear after 5 minutes, proceed.

**Step 4 — Promote to remaining units**

```bash
systemctl --user restart lyra-discord.service
systemctl --user restart lyra-clipool.service
systemctl --user restart lyra-hub.service
```

Restart hub last — it is the sole consumer of inbound queues.

**Step 5 — Final smoke test** after full promotion.

---

## Smoke test

Tests the critical path: hub receives a message, dispatches to clipool, clipool replies, hub forwards to adapter.

**Manual smoke test (always available):**

```bash
# Send a message to @ArylandAI on Telegram and confirm a reply arrives within 30s.
# Any reply (even an error message) confirms the NATS round-trip is working.
# Silence = broken.
```

**Log-based confirmation:**

```bash
# After sending the test message, check hub dispatched and received a reply:
journalctl --user -u lyra-hub --since "1 min ago" | grep -E "clipool|stream_gen|timeout|dispatch"

# Check NATS for permission violations:
journalctl --user -u lyra-nats --since "1 min ago" | grep -i "violation"
```

Pass criteria:
- Bot replies within 30 seconds
- No `stream_gen timeout` in hub logs
- No `Permissions Violation` in NATS logs

---

## Rollback

If the smoke test fails after canary deploy:

```bash
# Roll back to previous image tag
podman pull ghcr.io/roxabi/lyra:<previous-tag>
systemctl --user restart lyra-telegram.service
```

Previous image tag is in the release notes or `podman image ls ghcr.io/roxabi/lyra`.

If the failure is an ACL/NATS issue (permission violations in logs), see [nats-authconf-update.md](nats-authconf-update.md) rollback section instead.

---

## When to skip canary

- Config-only changes that do not touch connection or auth logic (e.g. prompt edits, agent config)
- Hotfixes where the canary delay would extend a live outage

In both cases, still run the smoke test after deploy.

---

## Cross-references

- [nats-authconf-update.md](nats-authconf-update.md) — ACL update + NATS reload
- [nkey-rotation.md](nkey-rotation.md) — credential rotation
- [nats-acl-inbox-case-postmortem.md](nats-acl-inbox-case-postmortem.md) — incident that motivated this runbook
