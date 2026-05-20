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

**2. Regenerate auth.conf and reload NATS**

```bash
make nats-regen-authconf
```

This runs `scripts/gen_nkeys.py` (entry point `lyra-acl genkeys --regen-authconf`) to re-derive `auth.conf` from all existing seeds, back up the previous `auth.conf`, recreate the Podman secret, then sends `podman kill -s HUP lyra-nats` to trigger a live reload (the in-container NATS server reloads its config on SIGHUP — the host `nats-server` CLI is not used in the Quadlet deployment).

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

Services reconnect automatically after a NATS reload — no service restart required unless the ACL change added a new identity whose seed is newly generated (in which case restart that service only). Reconnect typically completes in under 1 second on the Podman bridge network; if a service has not reconnected within 10 s, treat it as a failure and proceed to Rollback.

**5. Smoke test**

Send a message to the bot on any channel and confirm a reply arrives. This validates the full hub → clipool → hub reply path under the new ACL.

---

## Rollback

`lyra-acl genkeys --regen-authconf` (`scripts/gen_nkeys.py`) backs up `auth.conf` to `~/.lyra/nkeys/auth.conf.bak.<timestamp>` before overwriting. To revert:

```bash
# Replace TIMESTAMP with the backup suffix printed by `make nats-regen-authconf` in step 2
cp ~/.lyra/nkeys/auth.conf.bak.TIMESTAMP ~/.lyra/nkeys/auth.conf
make quadlet-secrets-install
podman kill -s HUP lyra-nats
```

Then revert the `acl-matrix.json` change in git and investigate before re-applying.

---

## Cross-references

- `deploy/nats/acl-matrix.json` — ACL SSoT
- `scripts/gen_nkeys.py` (entry point `lyra-acl`) — renders `auth.conf` from the matrix
- [nkey-rotation.md](nkey-rotation.md) — compromise rotation (seed replacement)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — provisioning invariants
