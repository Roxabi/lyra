# NATS auth.conf Update Runbook

## Scope

Routine updates to `acl-matrix.json`: adding/removing identities, changing publish/subscribe ACLs, inbox normalization. No seed file is replaced — this is not a compromise rotation. For compromise cases see [nkey-rotation.md](nkey-rotation.md).

---

## When to run

- After any change to `deploy/nats/acl-matrix.json` merged to `staging`
- After adding or removing a NATS identity

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

This runs `gen-nkeys.sh --regen-authconf` (re-derives `auth.conf` from all existing seeds, backs up previous `auth.conf`, recreates the Podman secret) then sends `nats-server --signal reload`.

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

Services reconnect automatically after a NATS reload — no service restart required unless the ACL change added a new identity whose seed is newly generated (in which case restart that service only).

**5. Smoke test**

Send a message to the bot on any channel and confirm a reply arrives. This validates the full hub → clipool → hub reply path under the new ACL.

---

## Rollback

`gen-nkeys.sh --regen-authconf` backs up `auth.conf` to `~/.lyra/nkeys/auth.conf.bak.<timestamp>` before overwriting. To revert:

```bash
# Replace TIMESTAMP with the value printed by gen-nkeys.sh in step 2
cp ~/.lyra/nkeys/auth.conf.bak.TIMESTAMP ~/.lyra/nkeys/auth.conf
make quadlet-secrets-install
nats-server --signal reload
```

Then revert the `acl-matrix.json` change in git and investigate before re-applying.

---

## Cross-references

- `deploy/nats/acl-matrix.json` — ACL SSoT
- `deploy/nats/gen-nkeys.sh` — renders `auth.conf` from the matrix
- [nkey-rotation.md](nkey-rotation.md) — compromise rotation (seed replacement)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — provisioning invariants
