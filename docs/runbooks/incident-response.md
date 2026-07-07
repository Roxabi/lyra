# Runbook — Incident response (P0)

Process for declaring, communicating, and closing a P0 (user-facing) incident.
Written after the 2026-04-27 NATS ACL incident (all four bot channels down
3h15m): no user notification was sent, no one owned communication, and there
were no criteria for declaring an incident in the first place. See
[`docs/history/nats-acl-inbox-case-postmortem.md`](../history/nats-acl-inbox-case-postmortem.md)
for the full timeline.

## Detection is currently dormant — read this first

**Automated proactive detection does not exist today.** Nothing in this
runbook pages anyone. Every trigger below is something a human has to notice
or go looking for.

What exists vs. what runs:

| Layer | State |
|---|---|
| Check logic | `check_nats_log_errors()` in `src/factory/monitoring/checks_log.py` scans container logs for `permissions violation` and returns a `CheckResult`. It works. |
| Escalation | `src/factory/monitoring/escalation.py` (`send_telegram_alert` / `_send_telegram_message`) can turn a failing check into a Telegram message. It works. |
| Scheduler | **Does not exist.** The host-side `lyra-monitor.{service,timer}` systemd timer that used to invoke the checks periodically was removed — `deploy/provision.sh` records it as "superseded by Monitoring v2, tracked in #1035" and explicitly keeps the Python module around "for spec mining" only. |
| Replacement | **Never shipped.** #1035 (Monitoring v2) closed without adding a runtime. Its architectural successor, ADR-091 (four observability planes), ratified plane ③ ("internal infra state") as pull-based (`/health/detail`, monitoring checks) but is itself "design-only ratification — no runtime ships." The current-truth doc confirms this is still the state: `docs/architecture/observability.md` — "alerting is manual (dashboard + runbooks)." |

Net effect: the check functions and the Telegram-send code both exist in
isolation, but nothing wires one to the other on a schedule. Detection today
means an operator reads `journalctl`, runs a monitoring check by hand, or a
user reports the bot is broken. The 2026-04-27 incident's **2h44m detection
gap is still open** — nothing shipped since then closes it.

Do not read "trigger criteria" below as "alert fires, declare P0." Read it as
"if you observe this, declare P0" — because right now, observing it is a
manual act.

The actual gap-closer (a minimal, container-native periodic check replacing
the old host-timer architecture) is tracked separately in **#2245** (thin
ADR-091 plane③ V1-pull slice). That work is out of scope here — this runbook
only defines the response process once an incident is noticed, by whatever
means.

## 1. Trigger criteria — what makes it a P0

Declare a P0 when any of the following is **observed and confirmed by an
operator**, sustained for **more than 5 minutes**:

- A user-facing bot channel (Telegram `lyra`/`aryl`, Discord `lyra`/`aryl`)
  stops replying to messages — the adapter receives input but no reply is
  ever sent, or replies time out.
- A manual monitoring check on a user-facing path fails — e.g.
  `check_nats_log_errors` run by hand, `/health/detail`, or
  `journalctl --user -u factory-<unit>` shows repeated errors.
- NATS logs show sustained `permissions violation` entries on a reply/inbox
  subject — the exact signature of the 2026-04-27 incident.
- A user reports "the bot isn't responding" and an operator reproduces it.

**Not a P0:** a single transient error that self-recovers, an internal job
failure with no user-facing symptom, or planned/announced maintenance.

## 2. Ownership

Because detection is manual, ownership follows discovery: **whoever notices
or confirms the P0 is the incident owner** for user notification (§3, §4)
until they hand it off explicitly (a message in the incident thread naming
the new owner). For factory today — a single-operator project — this
defaults to Mickael. Ownership does not end until the post-incident gate
(§5) is satisfied; "I fixed it" is not the same as "I closed it."

## 3. User notification template

Post to **every** affected user-facing channel (Telegram `lyra`/`aryl`,
Discord `lyra`/`aryl`) within **15 minutes** of declaring a P0. Do not assume
one channel covers everyone — the 2026-04-27 incident hit all four
simultaneously and none of them got a message.

```
⚠️ Service disruption — investigating

{bot name} isn't responding normally since ~{HH:MM} UTC.
Impact: {one line, e.g. "message replies are delayed or failing"}
We're investigating and will post an update within 30 minutes or upon resolution.
```

Fill in `{bot name}`, `{HH:MM UTC}` (first observed, not now), and `{impact}`
in plain, non-technical language.

## 4. Resolution notification template

Post to the same channels once the service is confirmed restored:

```
✅ Resolved — service restored

{bot name} is back to normal as of {HH:MM} UTC.
Cause: {one-line, non-technical summary}
Total impact: ~{duration}
Full write-up: {link, once the postmortem below is written}
```

## 5. Post-incident gate

**The incident is not closed** — regardless of how quickly the symptom was
fixed — until all three of the following are done:

- **(a) Root cause documented.** Add a postmortem under `docs/history/`
  following the shape of
  [`nats-acl-inbox-case-postmortem.md`](../history/nats-acl-inbox-case-postmortem.md)
  (summary, timeline, root cause, why-chain, fixes). If the incident is a
  variant of an existing postmortem's failure mode, update that file instead
  of forking a near-duplicate.
- **(b) Action items filed with owners.** Every fix identified in the
  postmortem gets a GitHub issue with an assignee — not just a checklist
  line buried in the doc. Use the normal issue-triage flow (labels, size,
  priority) so the item shows up in the same backlog as everything else.
- **(c) This runbook updated if it was missing or wrong.** If the trigger
  criteria didn't match what actually happened, notification took longer
  than 15 minutes, ownership was ambiguous, or a template needed fields it
  didn't have — fix this file as part of closing the incident, not as a
  someday follow-up.

## References

- [`docs/history/nats-acl-inbox-case-postmortem.md`](../history/nats-acl-inbox-case-postmortem.md) — source incident (2026-04-27), origin of every lesson above
- [`docs/architecture/observability.md`](../architecture/observability.md) — current truth on the four observability planes; confirms alerting is manual today
- #2245 — thin ADR-091 plane③ V1-pull slice; the actual detection-gap-closer, tracked and scoped separately from this runbook
