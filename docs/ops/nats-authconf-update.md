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
cd ~/projects/lyra
git pull
```

**2. Regenerate auth.conf and restart NATS**

```bash
make nats-regen-authconf
```

This runs `scripts/gen_nkeys.py` (entry point `lyra-acl genkeys --regen-authconf`) to re-derive `auth.conf` from all existing seeds, back up the previous `auth.conf`, recreate the Podman secret, then runs `systemctl --user restart lyra-nats` to recreate the container with the refreshed mount.

> **Why restart and not SIGHUP?** Podman secrets declared `type=mount` in `deploy/quadlet/lyra-nats.container` are tmpfs bind-mounts bound at container init. `podman secret create --replace` updates the secret store, but the file inside the running container still resolves to the old tmpfs content. `nats-server` re-reads its config path on SIGHUP, but the path itself is stale — so ACL changes silently fail to apply. Container recreation is the only way to refresh a mount-typed secret. Confirmed during PR #1292 deploy (2026-05-20); see #1293 for the broader ACL-hardening epic.
>
> **What about `type=env`?** It does not avoid the restart requirement. Podman injects `type=env` secrets into the container's environment at start; SIGHUP does not re-exec the entrypoint and does not update `environ`, so the value is also baked in. `type=env` would trade `type=mount`'s init-bound tmpfs for an `environ`-baked value with the additional cost of secret content being visible to `podman inspect`. Both forms require container recreation for ACL changes; `type=mount` is preferred by the hardening invariants in [`deploy/CLAUDE.md`](../../deploy/CLAUDE.md#hardening-invariants).

**3. Verify — no permission violations**

```bash
journalctl --user -u lyra-nats --since "2 min ago" | grep -i "violation\|error\|warn"
```

Expected: no output. Any `Permissions Violation` line means the new ACL does not match what a service is connecting with — see Rollback.

**4. Verify each service reconnected**

```bash
journalctl --user -u lyra-hub      --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u lyra-telegram --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u lyra-discord  --since "2 min ago" | grep -i "nats\|connected\|error"
journalctl --user -u lyra-clipool  --since "2 min ago" | grep -i "nats\|connected\|error"
```

Services reconnect automatically after the NATS restart — no service restart required unless the ACL change added a new identity whose seed is newly generated (in which case restart that service only). Reconnect typically completes in under 2 seconds on the Podman bridge network once `lyra-nats` is back up; if a service has not reconnected within 10 s, treat it as a failure and proceed to Rollback.

**5. Smoke test**

Send a message to the bot on any channel and confirm a reply arrives. This validates the full hub → clipool → hub reply path under the new ACL.

---

## External seed distribution

### When it runs

`lyra-acl genkeys --regenerate` (and the default full-provision path) exits 2 and emits an
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
scp ~/.lyra/nkeys/voice-client.seed $USER@roxabitower:~/.voicecli/nkeys/voice-client.seed
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

- CLI flag: `lyra-acl genkeys --ack-external-distribution`
- Schema: `deploy/nats/acl-matrix.json` → identity `.deploy.type=external`
- Source: `scripts/_modes.py::_emit_external_manifest`, `scripts/_modes.py::_operator_user`
- Issue: #1379

---

## Rollback

`lyra-acl genkeys --regen-authconf` (`scripts/gen_nkeys.py`) backs up `auth.conf` to `~/.lyra/nkeys/auth.conf.bak.<timestamp>` before overwriting. To revert:

```bash
# Replace TIMESTAMP with the backup suffix printed by `make nats-regen-authconf` in step 2
cp ~/.lyra/nkeys/auth.conf.bak.TIMESTAMP ~/.lyra/nkeys/auth.conf
# Rollback uses the full `quadlet-secrets-install` (all 5 secrets) — broader
# than the scoped forward path (`nats-regen-authconf` only touches lyra-nats-auth).
# Intentional: emergency rollback restores a known-good snapshot atomically.
make quadlet-secrets-install
systemctl --user restart lyra-nats
```

Then revert the `acl-matrix.json` change in git and investigate before re-applying.

---

## Cross-references

- `deploy/nats/acl-matrix.json` — ACL SSoT
- `scripts/gen_nkeys.py` (entry point `lyra-acl`) — renders `auth.conf` from the matrix
- [nkey-rotation.md](nkey-rotation.md) — compromise rotation (seed replacement)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — provisioning invariants
