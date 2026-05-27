# NATS Identity Lifecycle Runbook

## Overview

Each identity in `deploy/nats/acl-matrix.json` carries `status`, `created_at`, and (when retired) `retired_at`. These fields create an audit trail: you can see when each credential was provisioned, when it was decommissioned, and why — without silently discarding entries. A retired identity remains in the file for history but is excluded from `auth.conf` generation and from CI spec table rendering.

This runbook covers both adding new identities (the safe, non-rotating path) and retiring decommissioned ones.

---

## Adding an identity

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

**2. Run `make nats-add-identity NAME=<name>`.**

This single rootless verb chains: generate the new seed, re-render `auth.conf` preserving existing pubkeys, create the local Podman secret, and restart consuming services (`lyra-nats` and any active adapters). No `sudo` required.

```bash
make nats-add-identity NAME=new-worker
```

Expected output: `STATE=added` on the first run. A second run on the same host (full-consistency state) emits `STATE=noop` and skips all Podman and systemctl calls.

**3. Validate lifecycle fields.**

```bash
bash scripts/check-acl-matrix-retired.sh
```

Expected output: `ok — acl-matrix lifecycle fields valid`

**4. Commit.**

```bash
git add deploy/nats/acl-matrix.json
git commit -m "feat(nats): add <name> identity"
```

---

### Multi-host: running on M₁, M₂, Mₙ

Seeds and `auth.conf` propagate across hosts via Syncthing on `~/.lyra/nkeys/`. The Podman secret store does **not** auto-propagate. Run `make nats-add-identity NAME=<name>` on every host that consumes that identity — the same verb, no host-specific procedure.

How the gate works:

- lyra-acl emits `STATE=noop|repaired|added` based on filesystem state (seed present in `~/.lyra/nkeys/` and identity block present in `auth.conf`).
- The Makefile additionally checks `podman secret inspect lyra-nats-<NAME>`. Phase 2 (secret create + restart) runs when **either** lyra-acl mutated the filesystem **or** the local Podman secret is missing.

Per-host behavior:

- **Authoring host (e.g. M₁):** lyra-acl emits `STATE=added`; Phase 2 runs locally — creates the seed secret and refreshes `lyra-nats-auth`, then restarts `lyra-nats` and adapters.
- **Receiving host (e.g. M₂, after Syncthing delivered seed + auth.conf):** lyra-acl emits `STATE=noop` (filesystem already consistent), but `podman secret inspect lyra-nats-<NAME>` returns non-zero (secret not yet in the local Podman store) → Phase 2 still runs to create the local secret and restart any local Lyra units.
- **Non-consuming host:** lyra-acl emits `STATE=noop` AND `podman secret inspect` succeeds → true no-op; Phase 2 is skipped entirely.

The restart loop is `systemctl --user is-active`-gated over `{lyra-nats, lyra-hub, lyra-telegram, lyra-discord, lyra-clipool}` — nats first, then adapters in declared `After=` order. On a host with no Lyra units running, the loop is a complete no-op. The verb is safe to run on any host without knowledge of its topology.

---

### Recovery — Phase 2 fails after lyra-acl succeeded

**Scenario:** lyra-acl already mutated the filesystem (new seed + new `auth.conf` written and `STATE=added` emitted), but a subsequent `podman secret create --replace` or `systemctl --user restart` call failed (e.g. transient daemon error, stale socket, permissions hiccup).

**Recovery:** re-run `make nats-add-identity NAME=<name>`. On the second invocation, lyra-acl finds the seed and auth.conf block already consistent and emits `STATE=noop`. The Makefile gate then checks `podman secret inspect lyra-nats-<NAME>` — if the secret is missing or stale, Phase 2 runs again. `--replace` makes the Podman call safe regardless of prior state.

There is no double-rotation risk: lyra-acl's `added` path only generates a seed when the seed file is absent. The existing seed file on disk is preserved on every subsequent invocation.

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

A `--retire <name>` subcommand for `gen-nkeys.sh` is planned. It will automate steps 1–7 of the retiring flow above (update JSON, remove flows, validate, update spec, regen auth.conf, commit). Until it ships, follow this runbook manually.

---

## Cross-references

- [`deploy/nats/acl-matrix.json`](../../deploy/nats/acl-matrix.json) — identity registry
- [`deploy/nats/gen-nkeys.sh`](../../deploy/nats/gen-nkeys.sh) — seed generation and auth.conf rendering
- [`scripts/check-acl-matrix-retired.sh`](../../scripts/check-acl-matrix-retired.sh) — lifecycle field validator
- [`scripts/check-acl-matrix-spec.sh`](../../scripts/check-acl-matrix-spec.sh) — spec table sync checker
- [nkey Rotation Runbook](nkey-rotation.md) — for suspected seed compromise (different scenario)
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — declarative provisioning invariants
