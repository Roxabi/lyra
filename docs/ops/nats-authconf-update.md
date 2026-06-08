# NATS auth.conf Update Runbook

## Scope

Routine updates to `acl-matrix.json`: adding/removing identities, changing publish/subscribe ACLs, inbox normalization. No seed file is replaced — this is not a compromise rotation. For compromise cases see [nkey-rotation.md](nkey-rotation.md).

---

## When to run

- After any change to `deploy/nats/acl-matrix.json` merged to `staging`
- After adding or removing a NATS identity
- After upgrading the roxabi-nats SDK to a version that adds or changes NATS subjects (e.g. new readiness probe subjects, new metrics subjects). Cross-check against `acl-matrix.json` and run regen if new subjects are not covered.

---

## Steps

**1. Pull latest staging**

```bash
cd ~/projects/roxabi-factory
git pull
```

**2. Regenerate auth.conf and restart NATS**

```bash
make nats-regen-authconf
```

This runs `scripts/gen_nkeys.py` (entry point `factory-acl genkeys --regen-authconf`) to re-derive `auth.conf` from all existing seeds, back up the previous `auth.conf`, write the new `auth.conf` atomically to the host bind-mount path, then runs `systemctl --user restart factory-nats` to recreate the container.

> **Two reload paths — choose based on the change type:**
>
> **Path A — Pure identity add (`make nats-add-identity NAME=<x>`):**
> `auth.conf` is delivered as an inline bind mount (`Volume=%h/.roxabi/factory/nkeys/auth.conf:/etc/nats/nkeys/auth.conf:ro,z`) in `deploy/quadlet/factory-nats.container` (ADR-085). After writing the new `auth.conf` atomically on the host, the operator (or Makefile) issues `systemctl --user reload factory-nats`. The unit's `ExecReload=` fires `podman kill --signal=HUP factory-nats`; nats-server re-reads its config tree and the new identity is live immediately — **zero client restarts, zero dropped connections.** Use this path when only adding a new `U…` nkey with no changes to existing permission blocks.
>
> **Path B — ACL permission change (`make nats-regen-authconf`):**
> A `systemctl --user restart factory-nats` is required. The inline bind mount means the new `auth.conf` content on the host IS visible to the container (no stale tmpfs), so SIGHUP would technically re-read the correct file — however, #1390 identified that nats-server can serve stale subject-auth decisions after an ACL permissions change until the server is fully restarted. Container recreation is therefore required for any change that adds, removes, or restricts publish/subscribe rules in existing permission blocks. All 7 NATS clients (`FACTORY_NATS_CLIENTS`) are also restarted to flush cached auth state.
>
> **Summary table:**
>
> | Operation | Mechanism | Client restart? |
> |-----------|-----------|----------------|
> | Add new identity (`U…` key, no permission changes) | `make nats-add-identity` → SIGHUP reload | No |
> | Change ACL permission blocks | `make nats-regen-authconf` → restart | Yes (#1390) |

**3. Verify — no permission violations**

```bash
journalctl --user -u factory-nats --since "2 min ago" | grep -i "violation\|error\|warn"
```

Expected: no output. Any `Permissions Violation` line means the new ACL does not match what a service is connecting with — see Rollback.

**4. Verify each service reconnected**

```bash
journalctl --user -u factory-hub      --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u factory-telegram --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u factory-discord  --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u factory-clipool  --since "2 min ago" | grep -i "nats\|connected\|error"
```

Services reconnect automatically after the NATS restart — no service restart required unless the ACL change added a new identity whose seed is newly generated (in which case restart that service only). Reconnect typically completes in under 2 seconds on the Podman bridge network once `factory-nats` is back up; if a service has not reconnected within 10 s, treat it as a failure and proceed to Rollback.

**5. Smoke test**

Send a message to the bot on any channel and confirm a reply arrives. This validates the full hub → clipool → hub reply path under the new ACL.

---

## External seed distribution

### When it runs

`factory-acl genkeys --regenerate` (and the default full-provision path) exits 2 and emits an
scp manifest on stderr whenever any active identity has `deploy.type=external` in
`acl-matrix.json` and `--ack-external-distribution` is **not** passed. Seeds and `auth.conf`
are already committed at that point — only fan-out to the remote host is outstanding. Pass
`--ack-external-distribution` once you have copied the seeds to confirm the distribution was
handled.

### Current external identities

- `voice-client` → `roxabitower:~/.voicecli/nkeys/voice-client.seed`

> Currently: voice-client → roxabitower:~/.voicecli/nkeys/voice-client.seed
> (see `deploy/nats/acl-matrix.json` for live state — T4/#1379 adds `deploy.type` field)

### The scp template

The manifest printed to stderr has one line per external identity:

```
scp <local-seed-path> $USER@<host>:<target_path>
```

`$USER` is preserved from `SUDO_USER` when the operator ran under sudo; otherwise it is the
current `getpass.getuser()`. Example for the current matrix:

```bash
scp ~/.roxabi/factory/nkeys/voice-client.seed $USER@roxabitower:~/.voicecli/nkeys/voice-client.seed
```

Copy and run these lines after every `genkeys --regenerate` that touches external identities.

### What if I forget

On 2026-05-25/26, PR #1347 triggered a full seed regen. The `voice-client` seed was not
copied to `roxabitower`. Result: 14 h NATS auth lockout on M₂, ~30 permission-violation
errors per minute, and silent failure of `voicecli dictate nats` with no user-visible
indication that requests were being dropped. The runbook line "copy seeds manually" proved
insufficient as a safeguard. Issue #1379 introduced the fail-loud guard — exit 2 + manifest
— so the operator cannot skip fan-out without an explicit acknowledgement flag.

### Cross-references

- CLI flag: `factory-acl genkeys --ack-external-distribution`
- Schema: `deploy/nats/acl-matrix.json` → identity `.deploy.type=external`
- Source: `scripts/_modes.py::_emit_external_manifest`, `scripts/_modes.py::_operator_user`
- Issue: #1379

---

## Rollback

`factory-acl genkeys --regen-authconf` (`scripts/gen_nkeys.py`) backs up `auth.conf` to `~/.roxabi/factory/nkeys/auth.conf.bak.<timestamp>` before overwriting. To revert:

```bash
# Replace TIMESTAMP with the backup suffix printed by `make nats-regen-authconf` in step 2
cp ~/.roxabi/factory/nkeys/auth.conf.bak.TIMESTAMP ~/.roxabi/factory/nkeys/auth.conf
# auth.conf is an inline bind mount (ADR-085) — no secret recreate needed.
# The file on the host IS the file the container reads.
# Restart (not SIGHUP) because ACL permission blocks may have changed.
systemctl --user restart factory-nats
```

Then revert the `acl-matrix.json` change in git and investigate before re-applying.

---

---

## S3 / Sole-provisioner deploy ordering (ADR-079, #1525)

After a hub-sole-provisioner ACL tightening (where stream/KV CREATE grants are
removed from adapter identities and owned exclusively by the hub), the restart
sequence is **order-sensitive**:

1. Regenerate `auth.conf` + restart `factory-nats` (step 2 above, `make nats-regen-authconf`).
2. **Restart `factory-hub` first** — so it re-provisions stream `FACTORY_OUTBOUND_AUDIO`
   and KV bucket `KV_factory_outbound_audio_sent` before signalling `announce_hub_ready`.
3. **Only then** restart `factory-telegram` and `factory-discord` — they call `wait_for_hub`
   which blocks until the hub has finished provisioning, then bind (not create) stream+KV.

Reversing steps 2–3 (adapters before hub) will cause adapters to hit `wait_for_hub`
indefinitely until the hub starts and announces ready — harmless but will delay startup.
With the new ACL (CREATE grants removed from adapters), any adapter that bypasses
`wait_for_hub` and tries to create the stream directly would receive a NATS permission
violation.

**Caveat — auto-reconnect is NOT gated:** `wait_for_hub` only blocks at adapter
**startup**, not on automatic NATS reconnect after a NATS server restart. If `factory-nats`
is restarted while the hub and adapters are already running, all three processes
reconnect simultaneously. The hub will re-provision stream+KV on reconnect (ensure_stream
and ensure_kv are idempotent), but adapters may attempt their reconnect sequence before
the hub finishes. In that window, adapter audio consumers may log a degraded-boot
warning and fall back to `NullAudioConsumer` (ADR-079 S2). Audio resumes on the
next adapter restart. For planned NATS restarts, manually restart hub first, wait
for its `announce_hub_ready` log line, then restart adapters.

---

## Cross-references

- `deploy/nats/acl-matrix.json` — ACL SSoT
- `scripts/gen_nkeys.py` (entry point `factory-acl`) — renders `auth.conf` from the matrix
- [nkey-rotation.md](nkey-rotation.md) — compromise rotation (seed replacement)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — provisioning invariants
- [ADR-079](../architecture/adr/079-audio-nats-contract-axial-consolidation.mdx) — audio NATS axial migration, sole-provisioner pattern
- [ADR-085](../architecture/adr/085-public-aclbundle-bindmount-sighup.mdx) — auth.conf carve-out from type=mount, SIGHUP reload for identity-add
