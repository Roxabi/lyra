# NATS ACL Postmortem — Remaining Action Items

Verified against commit history and current codebase on 2026-04-28.
Source: [nats-acl-inbox-case-postmortem.md](nats-acl-inbox-case-postmortem.md)

---

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Done — verified in code/commits |
| ❌ | Not done |
| ⚠️ | Partial |

---

## What is already done (reference)

| Item | Commit / location |
|---|---|
| Fix 1 — lowercase `_inbox` normalization, all identities | `aed88001`, `4f3a02d6` |
| Fix 2 — explicit `_inbox.hub.>` in all responder publish ACLs | `5b3b6c4a` |
| Drop `allow_responses: true` from all identities | `5b3b6c4a` — hub/adapters explicit `false`; others omit field (NATS default = false) |
| Kill hardcoded identity list in gen-nkeys.sh — IDENTITIES[] driven from JSON SSoT | `load_matrix()` populates from acl-matrix.json |
| Alert on `permissions violation` in NATS logs | `src/lyra/monitoring/checks_log.py` — `check_nats_log_errors` |
| Alert on sustained `_dict_stream_gen timeout` in hub logs | `src/lyra/monitoring/checks_log.py:57` — `check_hub_dict_stream_gen_timeout` |
| NATS HTTP monitoring on 127.0.0.1 | `bcab1197` |
| Retire `tts-adapter`/`sst-adapter` from acl-matrix.json | `5b3b6c4a` |
| Fix gen-nkeys.sh missing `clipool-worker` in key-gen block | `5b3b6c4a` |
| NATS secret rotation runbook | `docs/ops/nkey-rotation.md` |
| Post-deploy smoke test + canary rollout procedure | `docs/ops/deploy-smoke-canary.md` |

---

## Remaining items

### 1. Fix 3 — `make test-acl` ACL integration test in CI ❌

**Priority:** P0 — this is the only fix that would have prevented the incident from shipping.
**Blocker:** none (Fix 1 is done; tests can use lowercase throughout).

The test suite must:

1. Generate real ephemeral nkeys via `nk -gen user` — one per identity. Do **not** use `--template-only` output; dummy pubkeys are rejected by a real NATS server.
2. Render `auth.conf` from `acl-matrix.json` using the ephemeral pubkeys (call `gen-nkeys.sh --template-only` path or refactor render step to accept injected pubkeys).
3. Spin up `nats-server -p 0` (random port to avoid CI conflicts). Poll stdout for `Server is ready` — no hardcoded `sleep`.
4. Connect as each identity using its nkey seed.
5. Assert each identity can subscribe/publish on all ACL-allowed subjects.
6. Assert cross-identity round-trips: hub→clipool→hub, hub→voice-tts→hub, hub→voice-stt→hub, hub→image-worker→hub.
7. Assert each identity is denied on subjects outside its ACL (expect `nats.errors.AuthorizationError` or permissions violation).
8. Include a fixture where `allow_responses` is removed from `gen-nkeys.sh` output — verify explicit ACL grants alone are sufficient. This documents Fix 2 as load-bearing and would catch any future regression.

**Gate:** Add as `pre-push` hook alongside `import_layers` in `.importlinter` / pre-push configuration. Any `acl-matrix.json` change must pass before reaching staging.

**Prerequisite:** `nats-server` binary must be present in CI and dev environments. Document installation or add as a CI setup step (e.g., `tools/install-nats-server.sh`).

**Limitation to document:** This validates ACL config correctness, not client code. A service connecting with a wrong `inbox_prefix` will still pass — the test only proves the ACL is internally consistent.

---

### 2. Phase 3 schema — `request_reply_flows` + generator derivation ❌

**Priority:** P1 — this is the durable structural fix for root cause 1 (ACL matrix is a permission list, not a topology graph). Fix 2 (explicit `_inbox.hub.>` in responder publish ACLs) is the correct immediate step and is done; Phase 3 makes it machine-enforced.

**Context:** Commits `fbde7faa` and `0249c925` (branch `#992`) planned and partially implemented this but did not land in `acl-matrix.json` or `gen-nkeys.sh`. Current state is hand-authored Fix 2 equivalents. The case-mismatch bug class is structurally still possible for any new identity.

#### 2a. Add `request_reply_flows` to `acl-matrix.json`

Declare all hub→responder flows explicitly at the top of the schema:

```json
{
  "request_reply_flows": [
    { "requester": "hub", "responder": "clipool-worker", "subject": "lyra.clipool.cmd" },
    { "requester": "hub", "responder": "voice-tts",      "subject": "lyra.voice.tts.request" },
    { "requester": "hub", "responder": "voice-stt",      "subject": "lyra.voice.stt.request" },
    { "requester": "hub", "responder": "image-worker",   "subject": "lyra.image.generate.request" },
    { "requester": "hub", "responder": "llm-worker",     "subject": "lyra.llm.request" }
  ],
  "identities": { ... }
}
```

Remove the hand-authored `_inbox.hub.>` entries from each responder's `publish` ACL — those become generator output, not input.

#### 2b. Update `gen-nkeys.sh` (or write `gen-acl.py`) to derive inbox ACLs from flows

For each flow `(requester=hub, responder=clipool-worker)`:
- Add `_inbox.{requester}.>` to requester's `subscribe` ACL (if not already present from its own identity block).
- Add `_inbox.{requester}.>` to responder's `publish` ACL.
- `allow_responses` is already false everywhere; this makes the explicit grant the sole auth path.

```bash
# In load_matrix(), after reading identities:
while read -r requester responder; do
  inbox="_inbox.${requester}.>"
  # append to SUB_ALLOW[requester] and PUB_ALLOW[responder]
done < <(jq -r '.request_reply_flows[] | "\(.requester) \(.responder)"' "$MATRIX")
```

Result: renaming hub's inbox prefix → change one place in `request_reply_flows` → all responder publish entries regenerate correctly. No second string to update.

#### 2c. Update `nats_connect()` in roxabi-nats to read inbox prefix from identity name

The connect-site and the generator must use the same string. Confirm that `nats_connect(identity_name="hub")` produces `inbox_prefix="_inbox.hub"` (already done per Fix 1 — verify still correct after generator refactor).

#### 2d. Extend Fix 3 test suite (after 2b is done)

Add a fixture that removes a flow declaration from `request_reply_flows` and asserts the corresponding derived ACL grant is absent from the generated `auth.conf`. This documents and tests topology derivation as load-bearing.

---

### 3. `acl-matrix.json` schema — `status: active|retired` field ❌

**Priority:** P1 — prevents monotonic ACL growth; retired identities have live credentials and active ACL grants (security surface).

**What is needed:**

1. Add `"status": "active"` field to all current identity blocks in `acl-matrix.json`.
2. `gen-nkeys.sh` `render_auth_conf`: skip any identity where `status == "retired"` — do not emit a `users[]` block for it.
3. `gen-nkeys.sh` `generate_nkeys` (full mode): skip `generate_nkey` call for retired identities — do not create or regenerate seeds.
4. Add a CI check that rejects any identity with `status == "retired"` and no `retired_at` date field. This forces retirement to be a documented, dated action rather than a passive omission.
5. Document the retirement process: set `status: retired`, add `retired_at: YYYY-MM-DD`, run `gen-nkeys.sh --regen-authconf`, run `make quadlet-secrets-install`, run `make lyra-nats reload`, verify logs show no reconnect from the retired identity.

**Reason this matters:** A future identity added to the JSON but later abandoned will accumulate live credentials and ACL grants on every `--regenerate` run unless retirement is an explicit, tooling-enforced step.

---

### 4. `/health/ready` split — liveness vs. NATS round-trip readiness ❌

**Priority:** P1 — `/health` currently conflates liveness (process alive) with readiness (can serve user traffic). Hub process alive and NATS subscription broken are decoupled states, as the 2026-04-27 incident demonstrated: the health endpoint was green throughout the 3h15m outage.

**Current state:** `src/lyra/bootstrap/infra/health.py` exposes `/health` (liveness, no auth) and `/health/detail` (full status, bearer token). No readiness probe exists.

**What is needed:**

Add `/health/ready` endpoint that performs a real NATS round-trip:

```python
@app.get("/health/ready")
async def health_ready() -> dict:
    # Send a request to lyra.system.ready and wait for reply
    # If nc is None (unified mode) or round-trip fails within 2s → return 503
    try:
        await nc.request("lyra.system.ready", b"", timeout=2.0)
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="NATS round-trip failed")
```

Update Quadlet unit healthcheck to use `/health/ready` instead of (or in addition to) `/health`:

```ini
HealthCmd=curl -sf http://localhost:8443/health/ready
HealthInterval=30s
HealthRetries=3
```

**Why this catches the incident scenario:** clipool publish ACL broken → hub→clipool round-trip fails → `/health/ready` returns 503 → container orchestration marks unit unhealthy → alert fires. Current `/health` probe would still return 200 in this scenario.

---

### 5. `make nats-rotate-secrets` atomic wrapper ❌

**Priority:** P1 — the current 4-step manual process is a routine hazard. Every ACL change is an opportunity to leave the system silently inconsistent (e.g., `make lyra-nats reload` executed before `make quadlet-secrets-install` silently reloads NATS against the old `auth.conf`).

**Current state:** `Makefile` has `nats-regen-authconf` (pull + regen) and `quadlet-secrets-install` (create Podman secrets) as separate targets with no dependency chain.

**What is needed:**

```makefile
nats-rotate-secrets: ## atomic: regen auth.conf → install secrets → reload NATS → verify
	@$(MAKE) nats-regen-authconf
	@$(MAKE) quadlet-secrets-install
	@ssh $(PROD) "systemctl --user reload lyra-nats.service || systemctl --user restart lyra-nats.service"
	@sleep 3
	@ssh $(PROD) "journalctl --user -u lyra-nats --since '10 seconds ago' | grep -i 'permission\|error\|fatal'" \
		&& echo "WARNING: errors detected after reload — check logs" \
		|| echo "No errors detected — rotation complete"
	@$(MAKE) voice-smoke
```

The post-reload verification step (grep for `permissions violation` in first 30s, run smoke test) is load-bearing — it turns a silent partial-apply into an immediate failure.

---

### 6. Incident response process ❌

**Priority:** P1 — the 2026-04-27 incident ran for 3h15m with no user notification, no defined owner, no trigger criteria.

**What is needed:** Create `docs/ops/incident-response.md` covering:

1. **Trigger criteria** — what constitutes a P0 incident requiring the process (example: any monitoring check failure sustained >5 min on a user-facing path).
2. **Owner** — who is responsible for user communication during an incident.
3. **User notification template** — a Telegram/Discord message template to send within 15 minutes of declaring an incident:
   > "⚠️ Lyra is experiencing issues ([short description]). All channels affected. ETA: investigating. Updates here."
4. **Resolution notification** — message template to send when service is restored.
5. **Post-incident gate** — incident is not closed until: (a) root cause documented, (b) action items filed as GitHub issues with owners, (c) runbook updated if the process was missing or wrong.

**Link from postmortem:** Add a "Incident response" section to `nats-acl-inbox-case-postmortem.md` pointing to the new doc.

---

### 7. Credential rotation policy ❌

**Priority:** P2 — no documented schedule, no triggering conditions beyond compromise, no rotation log.

**Current state:** `docs/ops/nkey-rotation.md` is a compromise-response runbook only. There is no periodic rotation policy and no rotation audit record.

**What is needed:**

1. **Rotation schedule:** document a maximum seed age (90 days recommended per postmortem). Add a comment block at the top of `acl-matrix.json` or a separate `docs/ops/credential-policy.md` with:
   - Max seed age: 90 days
   - Event-triggered rotation: suspected compromise, personnel change with prod access, seed file visible in logs
   - Scheduled rotation: quarterly (mark in calendar or set a cron reminder)

2. **Rotation log:** create `~/.lyra/nkeys/rotation-log.md` (or append to a fixed path) with an entry on every rotation:
   ```
   2026-04-28 | identities: hub, clipool-worker | reason: quarterly | operator: mickael
   ```
   Add a `gen-nkeys.sh --regenerate` post-action to append this entry automatically (prompt for reason).

3. **Age check:** add a `make check-seed-age` target that reads `mtime` of each seed file and warns if any exceeds 90 days. Wire into the monitoring checks or pre-push hook.

---

### 8. Graceful degradation on `_stream_gen` timeout ✅

**Verified 2026-04-28 — already handled.**

`StreamProcessor` (`src/lyra/core/processors/stream_processor.py:134`) detects when the stream ends without a `ResultLlmEvent` and emits `TextRenderEvent("Something went wrong. Please try again.", is_error=True)`. The user receives a message; there is no silent failure.

The only remaining gap is cosmetic: the error text is generic and does not distinguish "clipool unreachable" from other truncation causes. Not worth a dedicated issue.

---

### 9. Post-incident 24h verification ⚠️ (open)

**Status:** Still open per the postmortem action items table.

**What is needed:** Confirm zero `permissions violation` log lines in NATS container logs for a full 24h window after the last ACL change. Run:

```bash
ssh mickael@192.168.1.16 \
  "journalctl --user -u lyra-nats --since '24 hours ago' | grep -i 'permissions violation' | wc -l"
```

Expected output: `0`. If non-zero, investigate before closing this item.

---

## Priority summary

| # | Item | Priority | Effort estimate |
|---|---|---|---|
| 1 | Fix 3 — `make test-acl` CI gate | **P0** | Medium (new test infra + NATS binary in CI) |
| 2 | Phase 3 schema — `request_reply_flows` + generator derivation | **P1** | Large (schema change + gen-nkeys.sh refactor + test extension) |
| 3 | `acl-matrix.json` `status: active\|retired` field | **P1** | Small (JSON field + gen-nkeys.sh skip logic + CI check) |
| 4 | `/health/ready` split | **P1** | Small (new FastAPI endpoint + Quadlet healthcheck update) |
| 5 | `make nats-rotate-secrets` atomic wrapper | **P1** | Small (Makefile target + post-verify step) |
| 6 | Incident response process | **P1** | Small (doc + template) |
| 7 | Credential rotation policy + log | **P2** | Small (policy doc + gen-nkeys.sh append) |
| 8 | Graceful degradation on `_stream_gen` timeout | ~~P2~~ | ✅ already handled by StreamProcessor |
| 9 | Post-incident 24h verification | **open** | Trivial (one ssh command) |

Items 1, 3, 4, 5, 6 are unblocked and independent of each other.
Item 2 (Phase 3) depends on item 1 (Fix 3) being done first so the topology derivation is tested.
Item 2d (Fix 3 extension) depends on item 2b (generator) being done.
