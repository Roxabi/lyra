# nkey Rotation Runbook — Compromise Case

## Scope

This runbook applies **only when a specific nkey seed is suspected compromised** — meaning the seed file's raw content may have been observed by an unauthorized party (exfiltrated from disk, leaked in logs, captured in a backup, etc.). If you are here because auth.conf is out of date, a new identity is missing, or permissions blocks are wrong, stop: those cases are handled non-destructively by `gen-nkeys.sh --regen-authconf` as described in [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx).

**If you are not responding to a suspected compromise, you do not want this runbook.**

Rotation replaces the seed file (private key material) for one or more identities. The affected processes will authenticate with new credentials after restart. All other identities keep their existing seeds untouched.

---

## Identity → Systemd Unit Map

> Production runs Podman Quadlet units (as of #611). The restart commands in Step 5 use
> `systemctl --user` accordingly. NATS runs as `lyra-nats.service` (Quadlet container).

| Identity (seed file) | Systemd unit | Log command |
|---|---|---|
| `hub.seed` | `lyra-hub.service` | `journalctl --user -u lyra-hub` |
| `telegram-adapter.seed` | `lyra-telegram.service` | `journalctl --user -u lyra-telegram` |
| `discord-adapter.seed` | `lyra-discord.service` | `journalctl --user -u lyra-discord` |
| `clipool-worker.seed` | `lyra-clipool.service` | `journalctl --user -u lyra-clipool` |
| `voice-tts.seed` | `voicecli-tts.service` (voiceCLI project) | `journalctl --user -u voicecli-tts` |
| `voice-stt.seed` | `voicecli-stt.service` (voiceCLI project) | `journalctl --user -u voicecli-stt` |

All seeds live in `~/.lyra/nkeys/` on Machine 1. The merged `auth.conf` is stored as Podman secret `lyra-nats-auth`.

---

## 1. Pre-flight

**1.1 Confirm the compromise signal.**
Document what you observed: which seed, when, and how it was exposed. Do not proceed based on vague suspicion alone — rotation is disruptive. The evidence should be concrete (e.g., seed file visible in a public log, backup accessible to wrong party, file exfiltrated).

**1.2 Identify which identity (or identities) to rotate.**
List the affected seed filenames. Example: `telegram-adapter.seed`. If hub is compromised, treat all identities as potentially compromised and rotate all.

**1.3 Confirm SSH access to Machine 1.**

```bash
ssh mickael@192.168.1.16
```

**1.3a If voicecli workers are in scope (`voice-tts.seed` or `voice-stt.seed`), confirm TLS cert is in place.**
voicecli workers connect via `tls://127.0.0.1:4222` and require `/etc/nats/certs/ca.crt`. If this file is absent, the workers will fail to connect after restart regardless of nkey rotation status.

```bash
# On Machine 1:
ls -la /etc/nats/certs/ca.crt
```

Resolve any missing cert before proceeding. voicecli connection errors during verification (Step 6.2) may indicate a TLS issue rather than an nkey issue.

**1.4 Confirm a baseline before starting.**

> **TODO:** replace with `lyra ops verify` once implemented (ADR-046 invariant 5).

`lyra ops verify` is planned per ADR-046 Invariant 5 but not yet implemented. Until it is, run the manual equivalent:

```bash
# On Machine 1:
sudo ./deploy/nats/gen-nkeys.sh --show
# Verify seed count matches expected 10 identities.

systemctl --user status 'lyra-*.service'
# All units should be active (running) before you begin.
```

If any unit is already in a failed state unrelated to this rotation, investigate and resolve before continuing. A degraded baseline makes the verification step ambiguous.

---

## 2. Backup the Compromised Seed

For each identity being rotated, back up its seed before deletion. Use a timestamp suffix so multiple rotations are distinguishable.

```bash
# On Machine 1 — run once per identity being rotated.
# Replace IDENTITY with the identity name (e.g. telegram-adapter).

IDENTITY=telegram-adapter
TS=$(date +%Y%m%d-%H%M%S)
cp ~/.lyra/nkeys/${IDENTITY}.seed ~/.lyra/nkeys/${IDENTITY}.seed.bak-${TS}
chmod 0600 ~/.lyra/nkeys/${IDENTITY}.seed.bak-${TS}
```

The backup preserves the compromised material for forensic reference. It is never re-used to authenticate.

---

## 3. Delete the Seed and Regenerate auth.conf

Delete the seed file for each compromised identity, then run `--regen-authconf`. Per ADR-046 Invariant 3, the script auto-creates a new seed for any identity whose file is absent and renders a fresh auth.conf from all 10 identities.

```bash
# On Machine 1 — requires sudo.

# 3.1 Delete the compromised seed(s).
rm ~/.lyra/nkeys/${IDENTITY}.seed
# Repeat rm for each additional compromised identity.

# 3.2 Re-render auth.conf with the new public key(s).
cd ~/projects/lyra
sudo ./deploy/nats/gen-nkeys.sh --regen-authconf
```

Expected output includes:
- `[+] Created missing seed: <identity>` for each deleted seed
- `[+] Derived pubkey from existing seed: <identity>` for unchanged identities
- `[+] Backed up auth.conf → /etc/nats/nkeys/auth.conf.bak.<timestamp>`
- `[+] auth.conf re-rendered from 10 existing seeds.`
- `[+] Next: sudo systemctl reload nats.service`

If `nats-server` is on PATH and `/etc/nats/nats.conf` exists, the script validates the new config via `nats-server -t` before writing. A validation failure restores the backup automatically.

---

## 4. Update NATS secret and restart

```bash
# Recreate Podman secret with new auth.conf
make quadlet-secrets-install

# Restart NATS container to pick up new secret
systemctl --user restart lyra-nats.service
```

Record the restart timestamp — you will need it for the verification step:

```bash
RELOAD_TS=$(date -Iseconds)
echo "Restart timestamp: ${RELOAD_TS}"
```

The container restart evicts all existing connections. All clients will reconnect with new credentials automatically.

---

## 5. Rolling Restart Order

Restart affected units in this order: workers first, adapters second, hub last. Workers and adapters first — they are reconnect-tolerant (circuit breaker in roxabi-nats) and can queue at NATS while the hub is briefly down. Hub last — it is the sole consumer of inbound queues; restarting it last minimises the window where inbound messages could fill NATS queues with no consumer.

Only restart units that use a rotated identity. If only `telegram-adapter` was rotated, restart only `lyra-telegram`. If `hub` was rotated, restart all units.

**5.1 voicecli workers** (if `voice-tts.seed` or `voice-stt.seed` was rotated — voiceCLI project):

```bash
# voiceCLI Quadlet units (run from ~/projects/voiceCLI)
systemctl --user restart voicecli-tts.service
systemctl --user restart voicecli-stt.service
```

**5.2 imagecli gen worker** (if `image-worker.seed` was rotated — future Quadlet unit):

```bash
systemctl --user restart imagecli-gen.service
```

**5.3 Lyra adapters** (if any adapter seed was rotated):

```bash
systemctl --user restart lyra-telegram.service
systemctl --user restart lyra-discord.service
```

**5.4 Lyra hub** (if `hub.seed` was rotated):

```bash
systemctl --user restart lyra-hub.service
```

After each restart, wait for the unit to reach `active (running)` state before restarting the next one:

```bash
systemctl --user status 'lyra-*.service'
# Confirm the restarted unit shows active (running) before continuing.
```

---

## 6. Verification

> **TODO:** `lyra ops verify` planned per ADR-046 invariant 5 — replace manual checks below once CLI ships.

**6.1 Check for NATS auth errors** using the reload timestamp captured in Step 4:

```bash
scripts/check-nats-acls.sh --since "${RELOAD_TS}" --window 90 | tee ~/nkey-rotation-evidence.txt
```

Expected output on success: `OK: no Permissions Violation in nats.service over 90s window`

If violations are detected, the script prints the offending lines and exits 1. Jump to **Rollback** immediately.

**6.2 Check each restarted unit log for a successful NATS connection.**

All container stdout/stderr goes to journald. Check with `journalctl --user`:

```bash
# Hub
journalctl --user -u lyra-hub --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"

# Telegram adapter
journalctl --user -u lyra-telegram --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"

# Discord adapter
journalctl --user -u lyra-discord --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"

# voicecli workers (if rotated)
# Note: voicecli connects via tls://127.0.0.1:4222 — connection errors here may
# indicate a TLS issue (/etc/nats/certs/ca.crt) rather than an nkey issue.
journalctl --user -u voicecli-tts --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"
journalctl --user -u voicecli-stt --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"
```

**6.3 Confirm unit states:**

```bash
systemctl --user status 'lyra-*.service'
```

All units should show `active (running)`. Any unit in `failed` state immediately after restart indicates an auth failure — see Rollback.

**6.4 Send a test message end-to-end:**
Send a message through Telegram or Discord to the bot and confirm a response arrives. This exercises the full hub → adapter round-trip with the new credentials.

**6.5 Verify the new seed is in place and perms are correct:**

```bash
ls -la ~/.lyra/nkeys/ | grep "${IDENTITY}"
# Should show 0600 permissions, owner mickael, no backup file as active seed.
```

---

## 7. Rollback

**When to trigger:** any program in FATAL or BACKOFF state after restart, `check-nats-acls.sh` exits 1, auth errors visible in logs, or end-to-end test fails.

Rollback restores the pre-rotation seed and auth.conf so the old credentials work again. This undoes the rotation — the compromised seed is re-activated temporarily. Treat rollback as an incident escalation path, not a routine step.

**7.1 Identify the backup files:**

```bash
ls ~/.lyra/nkeys/*.bak-*
# Note the timestamp suffix from Step 2.

ls /etc/nats/nkeys/auth.conf.bak.*
# Note the backup created by --regen-authconf in Step 3.
```

> **WARNING:** The compromised seed becomes live again the moment `systemctl reload` runs in Step 7.3. Before proceeding: (a) record the current time and reason for rollback in your incident log; (b) treat this rollback as a temporary measure only — a second rotation attempt must follow within 24 h once the root cause of the rotation failure is resolved.

**7.2 Restore the compromised seed:**

```bash
# Replace BAK_TS with the actual timestamp from your Step 2 output (format: YYYYMMDD-HHMMSS).
BAK_TS=YYYYMMDD-HHMMSS  # ← replace with timestamp from Step 2 output

cp ~/.lyra/nkeys/${IDENTITY}.seed.bak-${BAK_TS} ~/.lyra/nkeys/${IDENTITY}.seed
chmod 0600 ~/.lyra/nkeys/${IDENTITY}.seed
```

**7.3 Restore auth.conf and restart NATS:**

```bash
# Replace CONF_BAK with the actual backup filename from Step 3 output (format: YYYYMMDD-HHMMSS).
CONF_BAK=~/.lyra/nkeys/auth.conf.bak.YYYYMMDD-HHMMSS  # ← replace with timestamp from Step 3 output

cp "${CONF_BAK}" ~/.lyra/nkeys/auth.conf
make quadlet-secrets-install   # recreate Podman secret
systemctl --user restart lyra-nats.service
```

**7.4 Reverse-order restart** (workers first, hub last — same order as Step 5).

Restart one unit at a time and confirm each reaches `active (running)` before continuing.

```bash
systemctl --user restart voicecli-tts.service
systemctl --user restart voicecli-stt.service
systemctl --user status voicecli-tts.service voicecli-stt.service

systemctl --user restart lyra-telegram.service
systemctl --user restart lyra-discord.service
systemctl --user status lyra-telegram.service lyra-discord.service

systemctl --user restart lyra-hub.service
systemctl --user status lyra-hub.service
```

**7.5 Re-run verification** (Step 6) to confirm the rollback restored service. Then escalate: the rotation failed, the compromised seed is live again, and the compromise signal must be reassessed before the next attempt.

---

## 8. Backup Cleanup

After verification passes (Step 6), dispose of the seed backup. Compromised key material should not persist indefinitely in the live-seed directory — an idle backup file is still a leak vector if the directory is later exposed.

**Option A — delete:**

```bash
rm ~/.lyra/nkeys/${IDENTITY}.seed.bak-${TS}
```

**Option B — move to forensics archive:**

```bash
mkdir -p ~/.lyra/forensics
mv ~/.lyra/nkeys/${IDENTITY}.seed.bak-${TS} ~/.lyra/forensics/
```

Use Option B if you need to preserve the seed for incident investigation. In either case, confirm no `.bak-*` file remains in `~/.lyra/nkeys/`:

```bash
ls ~/.lyra/nkeys/*.bak-* 2>/dev/null && echo "WARNING: backup files still present"
```

> **TODO:** consider automating backup cleanup via a retention hook in gen-nkeys.sh.

---

## 9. Cross-References

- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — declarative provisioning invariants, `--regen-authconf` semantics, `lyra ops verify` (Invariant 5, planned)
- [#561](https://github.com/Roxabi/lyra/issues/561) — parent epic (NATS nkey provisioning)
- [#714](https://github.com/Roxabi/lyra/issues/714) — per-role ACL rework
- [`deploy/nats/gen-nkeys.sh`](../../deploy/nats/gen-nkeys.sh) — seed generation and auth.conf rendering
- [`scripts/check-nats-acls.sh`](../../scripts/check-nats-acls.sh) — ACL violation detector used in Step 6.1
