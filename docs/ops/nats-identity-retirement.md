# NATS Identity Retirement Runbook

## Overview

Each identity in `deploy/nats/acl-matrix.json` carries `status`, `created_at`, and (when retired) `retired_at`. These fields create an audit trail: you can see when each credential was provisioned, when it was decommissioned, and why — without silently discarding entries. A retired identity remains in the file for history but is excluded from `auth.conf` generation and from CI spec table rendering.

---

## Retiring an identity

**1. Update `acl-matrix.json`.**

Set `"status": "retired"` and add `"retired_at"` on the identity being decommissioned:

```json
"old-worker": {
  "status": "retired",
  "created_at": "2026-01-10",
  "retired_at": "2026-04-28",
  ...
}
```

**2. Remove from `request_reply_flows`.**

If the identity appears as `requester` or `responder` in any flow entry, remove those entries. A retired identity in `request_reply_flows` is a CI error.

**3. Validate lifecycle fields.**

```bash
bash scripts/check-acl-matrix-retired.sh  # validates status, created_at, retired_at on ALL identities
```

Expected output: `ok — acl-matrix lifecycle fields valid`

**4. Refresh the ACL spec table.**

```bash
bash scripts/check-acl-matrix-spec.sh --update
```

This rewrites the sentinel-bracketed table in `artifacts/specs/706-per-role-nkeys-acls-spec.mdx` to reflect the current active identity set.

**5. Regenerate `auth.conf`.**

`gen-nkeys.sh` skips retired identities when rendering `auth.conf`. The retired identity's public key is no longer present in any permissions block after this step.

```bash
lyra-acl genkeys --regen-authconf
```

**6. Commit.**

```bash
git add deploy/nats/acl-matrix.json artifacts/specs/706-per-role-nkeys-acls-spec.mdx
git commit -m "chore(nats): retire <name> identity"
```

**7. Reload NATS.**

```bash
sudo systemctl reload nats
# or, if systemd is not managing NATS directly:
nats-server --signal reload
```

**8. Verify the retired identity is rejected.**

Attempt to connect with the retired seed and confirm NATS returns an auth error. Any active services should be unaffected; check their logs for unexpected reconnect errors:

```bash
journalctl --user -u lyra-hub --since "2 min ago" | grep -i "auth\|error\|nats"
journalctl --user -u lyra-telegram --since "2 min ago" | grep -i "auth\|error\|nats"
```

**9. Seed file decision.**

The seed file at `~/.lyra/nkeys/<name>.seed` remains on disk. NATS rejects the credential regardless once `auth.conf` is reloaded. Once you have confirmed the identity is fully offline and no rollback is needed, you may shred the file:

```bash
shred -u ~/.lyra/nkeys/<name>.seed
```

This is optional — the file is inert after step 7.

---

## Adding a new identity

**1. Add the entry to `acl-matrix.json`** with `"status": "active"` and today's date as `"created_at"`:

```json
"new-worker": {
  "status": "active",
  "created_at": "2026-04-28",
  "owner": "<project>",
  "description": "...",
  "allow_responses": false,
  "publish": [...],
  "subscribe": [...]
}
```

If the identity participates in request-reply, add the corresponding entry to `request_reply_flows`.

**2. Validate and regenerate.**

```bash
bash scripts/check-acl-matrix-retired.sh  # validates status, created_at on ALL identities (not just the new one)
bash scripts/check-acl-matrix-spec.sh --update
lyra-acl genkeys --regen-authconf
```

`gen-nkeys.sh` creates `~/.lyra/nkeys/<name>.seed` if absent, derives the public key, and re-renders `auth.conf`.

**3. Commit and reload.**

```bash
git add deploy/nats/acl-matrix.json artifacts/specs/706-per-role-nkeys-acls-spec.mdx
git commit -m "feat(nats): add <name> identity"
sudo systemctl reload nats
```

---

## CI checks — what `check-acl-matrix-retired.sh` validates

The script iterates every identity in `acl-matrix.json` and asserts:

| Check | Applies to |
|---|---|
| `status` field is present | all identities |
| `created_at` field is present and matches `YYYY-MM-DD` | all identities |
| `retired_at` field is present and matches `YYYY-MM-DD` | retired identities only |
| identity does not appear in `request_reply_flows` | retired identities only |

Exit 0 on success; exit 1 with per-error messages on failure. The check runs in CI on every push that touches `acl-matrix.json`.

---

## Future

A `--retire <name>` subcommand for `gen-nkeys.sh` is planned. It will automate steps 1–7 above (update JSON, remove flows, validate, update spec, regen auth.conf, commit). Until it ships, follow this runbook manually.

---

## Cross-references

- [`deploy/nats/acl-matrix.json`](../../deploy/nats/acl-matrix.json) — identity registry
- [`deploy/nats/gen-nkeys.sh`](../../deploy/nats/gen-nkeys.sh) — seed generation and auth.conf rendering
- [`scripts/check-acl-matrix-retired.sh`](../../scripts/check-acl-matrix-retired.sh) — lifecycle field validator
- [`scripts/check-acl-matrix-spec.sh`](../../scripts/check-acl-matrix-spec.sh) — spec table sync checker
- [nkey Rotation Runbook](nkey-rotation.md) — for suspected seed compromise (different scenario)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — declarative provisioning invariants
