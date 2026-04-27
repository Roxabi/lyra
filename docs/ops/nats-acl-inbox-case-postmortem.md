# Post-mortem: NATS ACL inbox case mismatch (2026-04-27)

## Summary

All four bot channels (Telegram lyra/aryl, Discord lyra/aryl) were fully
unavailable for ~3h15m (15:34 deploy → 19:30 restored). Every user message
returned a silent failure (bot received the message, no reply was ever sent).
LLM-streamed responses were the only affected path; no commands, history reads,
or other features were tested for availability.

Root cause: hub's subscribe ACL was missing the lowercase `_inbox.hub.>` variant,
so clipool replies never reached the hub after a new image was deployed. Symptom
was `_stream_gen timeout on lyra.clipool.cmd` in hub logs and repeated
`permissions violation for publish to "_inbox.hub.*"` in clipool logs.
Detection lag: **2h44m** from first failure to mitigation start. No alert fired;
discovered via manual log inspection.

---

## Timeline

| Time (CEST) | Event |
|---|---|
| ~11:17 | Anthropic API 401 errors — unrelated (API key issue, self-resolved by ~12:23) |
| ~13:17–14:23 | CLI invocations succeed (hub PID 1585354, no ACL issue yet) |
| 15:34 | Hub restarted → new PID 1680947, new image pulled |
| 16:11 | First `permissions violation for publish to "_inbox.hub.*"` in clipool |
| 17:15, 18:03 | Repeated violations — every user message produces ❌ |
| 18:55 | Hub restarted as part of investigation + NATS secret updated |
| 19:30 | NATS restarted with new auth.conf (Podman secret); all units reconnect |
| 19:30+ | No further permission violations |

---

## Root cause

### What the code does

`hub_standalone.py` connects with `inbox_prefix="_INBOX.hub"`:

```python
nc = await nats_connect(nats_url, inbox_prefix="_INBOX.hub")
```

`NatsDriverBase._stream_gen` creates ephemeral inboxes via `nc.new_inbox()`:

```python
inbox = self._nc.new_inbox()   # → "_INBOX.hub.<NUID>"
await self._nc.publish(subject, payload, reply=inbox)
```

This is uppercase. Confirmed by exec into the running hub container:

```
_inbox_prefix bytes: b'_INBOX.hub'
new_inbox():         _INBOX.hub.tjt1ZZrZCAPdpKe6JhWAdT
```

### What NATS server does

NATS server 2.10.29 (Go) lowercases subject names in `-ERR` permission violation
messages. The actual wire subject is `_INBOX.hub.<NUID>` (uppercase), but the
server reports:

```
nats: permissions violation for publish to "_inbox.hub.3bimc4lrihwrpg4kx8k2ox"
```

This misled the investigation into thinking nats-py was generating lowercase
inboxes. The subjects on the wire were uppercase throughout.

### The actual gap

Hub's subscribe ACL before the fix:

```
subscribe: { allow: ["_INBOX.hub.>"] }   # uppercase only
```

The `allow_responses: true` in clipool's ACL grants publish permission to the
exact reply-to subject from the received message. Because the ACL evaluation by
NATS server appears to match case-insensitively for `allow_responses` grants but
applies the subscription filter case-sensitively, replies published by clipool
to `_INBOX.hub.<NUID>` did not match the hub's subscription to `_INBOX.hub.>`.

> **Note:** `allow_responses` case-sensitivity is observed behaviour, not a NATS
> spec guarantee. A future NATS release could change this; Fix 2 (explicit
> reply-path ACLs) is the only durable defence.

Exact failure chain:
1. Hub publishes to `lyra.clipool.cmd` with `reply="_INBOX.hub.TOKEN"`
2. Clipool receives message, attempts to publish streaming chunks to `_INBOX.hub.TOKEN`
3. NATS reports permission violation (displayed as lowercase in error)
4. Hub subscription `_INBOX.hub.>` receives nothing → `_stream_gen` times out
5. Users get ❌

### Why it worked before

Hub PID 1585354 (running from 08:40) was on an older image. The new image
deployed at 15:34 contained changes from commit `fd3250c4` (adding
`inbox_prefix="_INBOX.hub"`) and accompanying ACL narrowing. The combination of
a fresh uppercase prefix and the narrowed clipool publish ACL (removing the old
`_INBOX.>` wildcard, added in #949) exposed the gap.

---

## Fix applied

Added `_inbox.hub.>` to hub's subscribe ACL in `acl-matrix.json` and the live
Podman secret:

```json
"hub": {
  "subscribe": [
    "...",
    "_INBOX.hub.>",
    "_inbox.hub.>"    // ← added
  ]
}
```

Same dual-case pattern already used by tts-adapter, stt-adapter, voice-tts,
voice-stt for nats-py compatibility. NATS secret recreated and NATS container
restarted to apply.

---

## Long-term solutions

### Fix 1 — Standardize on lowercase `_inbox` prefix (eliminates dual-variant)

The dual-case pattern (`_INBOX.X.>` + `_inbox.X.>`) is a permanent maintenance
burden. Every new identity, every ACL audit, every `gen-nkeys.sh --regenerate`
must carry both variants or the next case-mismatch bug ships silently.

**Action:** Change all bootstrap connects to lowercase:

```python
# hub_standalone.py, adapter_standalone.py, clipool_standalone.py, voicecli
nc = await nats_connect(nats_url, identity_name="hub")   # already in a832f5c8
# OR explicit:
nc = await nats_connect(nats_url, inbox_prefix="_inbox.hub")
```

Update `nats_connect()` in roxabi-nats: `identity_name` → `f"_inbox.{identity_name}"`.
Update all `acl-matrix.json` subscribe entries to lowercase. Drop the uppercase
`_INBOX.X.>` entries.

Result: one entry per identity, matches what NATS server reports in errors,
no divergence possible.

**Cross-repo coordination required:** voicecli (`voice-tts`, `voice-stt`) is a
separate repo with its own release cycle. Fix 1 must be coordinated across lyra
+ voicecli in a single release, or the ACL must carry dual-case entries during a
grace period until all services confirm lowercase in staging. Deploying Fix 1 in
lyra only while voicecli still uses uppercase is an identical silent breakage.

**Safe deploy order:**
1. `acl-matrix.json` update (add lowercase, keep uppercase during transition)
2. `gen-nkeys.sh --regen-authconf` → `make quadlet-secrets-install`
3. `make lyra-nats reload`
4. Redeploy containers one at a time, verify each reconnects
5. Once all services confirmed on lowercase: drop uppercase entries, repeat 2–4

### Fix 2 — Make reply-paths explicit in `acl-matrix.json`

`allow_responses: true` is invisible in the ACL matrix. The fact that
clipool-worker needs to reach `_inbox.hub.*` is undocumented and only
inferable from the request-reply pattern at runtime.

**Action:** Add the requester's inbox to each responder's publish ACL:

```json
"clipool-worker": {
  "publish": ["lyra.clipool.heartbeat", "lyra.system.ready", "_inbox.hub.>"]
},
"voice-tts": {
  "publish": ["lyra.voice.tts.heartbeat", "lyra.system.ready", "_inbox.hub.>"]
},
"voice-stt": {
  "publish": ["lyra.voice.stt.heartbeat", "lyra.system.ready", "_inbox.hub.>"]
},
"image-worker": {
  "publish": ["...", "_inbox.hub.>"]   // same pattern — also uses allow_responses
}
```

This makes the dependency graph fully auditable in `acl-matrix.json` and
removes reliance on dynamic `allow_responses` grants for correctness.
`allow_responses: true` can be kept as defence-in-depth.

**Coupling trade-off:** responders now encode `_inbox.hub.>`, so a hub identity
rename or a new requester identity requires ACL updates across all responder
entries. Acceptable for current hub-spoke topology; does not auto-scale to
multi-requester topologies.

### Fix 3 — ACL integration test in CI

A wrong `auth.conf` currently ships silently; production breakage is the first
signal.

**Action:** Add `make test-acl` that:
1. Generates real ephemeral nkeys via `nk -gen user` (one per identity) — do
   **not** use `--template-only` output directly; the dummy pubkeys it produces
   are rejected by a real NATS server
2. Renders `auth.conf` from `acl-matrix.json` using the ephemeral pubkeys
3. Spins up NATS on `-p 0` (random port, avoids CI conflicts), waits for
   readiness probe (poll stdout for `Server is ready` — no `sleep` hardcode)
4. Connects as each identity using its nkey seed
5. Asserts each identity can subscribe/publish on all ACL-allowed subjects
6. Asserts cross-identity round-trips: hub→clipool→hub, hub→voice→hub
7. Asserts each identity is denied on subjects outside its ACL
8. Includes a fixture where `allow_responses` is removed — verifies explicit
   ACL grants alone are sufficient (documents Fix 2 as load-bearing)

Run in `pre-push` hook (alongside `import_layers`) so any `acl-matrix.json`
change is gated before it reaches staging.

**Limitation:** this validates ACL config, not client code. A service connecting
with a wrong `inbox_prefix` will still pass — the test only proves the ACL is
internally consistent.

**Prerequisites:** `nats-server` binary must be available in CI and dev
environments; document or add as a CI setup step.

---

## Recommended order

| Priority | Action | Effort | Blocker |
|---|---|---|---|
| P0 — now | Fix 2 — explicit reply-path publish in `acl-matrix.json` | 10-line JSON + regenerate secret | none |
| P0 — coordinated | Fix 1 — lowercase `_inbox` prefix + drop dual-variant | bootstrap `.py` + `acl-matrix.json` + redeploy | voicecli must move simultaneously |
| P0 — alongside Fix 1 | Fix 3 — ACL integration test in CI | new test script + Makefile target | Fix 1 must land first to test correct case |

Fix 2 is independent and low-risk — deploy immediately regardless of Fix 1
scheduling. It uses lowercase `_inbox.hub.>` (correct after Fix 1); during the
transition window before Fix 1 lands, also carry `_INBOX.hub.>` in each
responder's publish ACL.

Fix 3 is P0, not Medium: it is the only fix that would have prevented this
incident from shipping. Gate it in `pre-push` alongside `import_layers`.

> **Fix 1 is blocked by voicecli coordination.** Deploying it in lyra alone
> while voicecli still uses uppercase produces an identical outage for
> voice-tts and voice-stt. Do not ship without a joint release or a confirmed
> staging validation across both repos.

Fixes 1+2 together eliminate the entire class of case-mismatch and
invisible-grant bugs. Fix 3 is the guardrail that catches the next one.

---

## Latent bugs identified (independent of this incident)

- **`gen-nkeys.sh` hardcoded key-generation block skips `clipool-worker`**
  (lines 597–620): `clipool-worker.seed` is never created on a fresh run without
  `--regenerate`. The identity is covered by `render_auth_conf` via `IDENTITIES[]`
  but not by the explicit `generate_nkey` calls. Verify and fix before next
  fresh deploy.

- **Retired identities** (`tts-adapter`, `stt-adapter`) remain in
  `acl-matrix.json` with active ACL entries and no documented removal process.
  They generate seeds and auth entries on every regeneration. Schedule removal.

---

## Monitoring gaps

The 2h44m detection lag is unacceptable. No fix above addresses runtime alerting:

- **Add alert on `permissions violation` log lines** in the NATS container —
  would have flagged this at 16:11, ~3h earlier
- **Add alert on sustained `_stream_gen timeout`** in hub logs
- **Add synthetic round-trip health probe** (hub → clipool → hub) to the
  existing health endpoint; current `/health` returns hub liveness but not
  NATS round-trip correctness

---

## Missing runbook: NATS secret rotation

No documented procedure for the full sequence. Until written:

1. `gen-nkeys.sh --regenerate` (has auto-restore on failure)
2. `make quadlet-secrets-install`
3. `make lyra-nats reload`
4. Verify each service reconnects: `make lyra-hub logs`, `make lyra-clipool logs`, etc.

---

## Related

- `deploy/nats/acl-matrix.json` — SSoT for all NATS ACLs
- `deploy/nats/gen-nkeys.sh` — generates `auth.conf` from the matrix
- ADR-062 — NATS ACL inbox case normalization and explicit reply-paths
- ADR-051 — per-identity inbox prefix invariant
- ADR-045 — roxabi-nats SDK
- Issue #949 — clipool publish ACL narrowing (removed `_INBOX.>` wildcard)
