# nkey Rotation Runbook — Compromise Case

## Scope

This runbook applies **only when a specific nkey seed is suspected compromised** — meaning the seed file's raw content may have been observed by an unauthorized party (exfiltrated from disk, leaked in logs, captured in a backup, etc.). If you are here because auth.conf is out of date, a new identity is missing, or permissions blocks are wrong, stop: those cases are handled non-destructively by `gen-nkeys.sh --regen-authconf` as described in [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx).

**If you are not responding to a suspected compromise, you do not want this runbook.**

Rotation replaces the seed file (private key material) for one or more identities. The affected processes will authenticate with new credentials after restart. All other identities keep their existing seeds untouched.

---

## Identity → Supervisor Program Map

| Identity (seed file) | Supervisor program | Log path |
|---|---|---|
| `hub.seed` | `lyra_hub` | `~/.local/state/lyra/logs/lyra_hub.log` |
| `telegram-adapter.seed` | `lyra_telegram` | `~/.local/state/lyra/logs/lyra_telegram.log` |
| `discord-adapter.seed` | `lyra_discord` | `~/.local/state/lyra/logs/lyra_discord.log` |
| `tts-adapter.seed` | `lyra_tts` | `~/.local/state/lyra/logs/lyra_tts.log` |
| `stt-adapter.seed` | `lyra_stt` | `~/.local/state/lyra/logs/lyra_stt.log` |
| `voice-tts.seed` | `voicecli_tts` | `~/.local/state/voicecli/logs/voicecli_tts.log` |
| `voice-stt.seed` | `voicecli_stt` | `~/.local/state/voicecli/logs/voicecli_stt.log` |
| `llm-worker.seed` | _(no supervisor program yet)_ | — |
| `image-worker.seed` | `lyra_imagecli_gen` | `~/.local/state/lyra/logs/lyra_imagecli_gen.log` |
| `monitor.seed` | _(no supervisor program yet)_ | — |

All seeds live in `~/.lyra/nkeys/` on Machine 1. Live auth.conf lives at `/etc/nats/nkeys/auth.conf`.

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

**1.4 Confirm a baseline before starting.**
`lyra ops verify` is planned per ADR-046 Invariant 5 but not yet implemented. Until it is, run the manual equivalent:

```bash
# On Machine 1:
sudo ./deploy/nats/gen-nkeys.sh --show
# Verify seed count matches expected 10 identities.

supervisorctl status
# All programs should be RUNNING before you begin.
```

If any program is already in a FATAL or BACKOFF state unrelated to this rotation, investigate and resolve before continuing. A degraded baseline makes the verification step ambiguous.

---

## 2. Backup the Compromised Seed

For each identity being rotated, back up its seed before deletion. Use a timestamp suffix so multiple rotations are distinguishable.

```bash
# On Machine 1 — run once per identity being rotated.
# Replace IDENTITY with the identity name (e.g. telegram-adapter).

IDENTITY=telegram-adapter
TS=$(date +%Y%m%d-%H%M)
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

## 4. Reload NATS

```bash
sudo systemctl reload nats.service
```

This sends `SIGHUP` to the running `nats-server` process. The server re-reads `auth.conf` in place. Existing authenticated connections are not dropped immediately — they continue with their current authentication state until they reconnect. The old public key is invalidated for new connection attempts as soon as the reload completes.

Record the reload timestamp — you will need it for the verification step:

```bash
RELOAD_TS=$(date -Iseconds)
echo "Reload timestamp: ${RELOAD_TS}"
```

---

## 5. Rolling Restart Order

Restart affected programs in this order: workers first, adapters second, hub last. The hub is last because losing its seed mid-flight drops all queued in-flight messages. Adapters are reconnect-tolerant and will resume once the hub is back.

Only restart programs that use a rotated identity. If only `telegram-adapter` was rotated, restart only `lyra_telegram`. If `hub` was rotated, restart all programs.

**5.1 voicecli workers** (if `voice-tts.seed` or `voice-stt.seed` was rotated):

```bash
supervisorctl restart voicecli_tts
supervisorctl restart voicecli_stt
```

**5.2 imagecli gen worker** (if `image-worker.seed` was rotated):

```bash
supervisorctl restart lyra_imagecli_gen
```

**5.3 Lyra adapters** (if any adapter seed was rotated):

```bash
supervisorctl restart lyra_telegram
supervisorctl restart lyra_discord
supervisorctl restart lyra_tts
supervisorctl restart lyra_stt
```

**5.4 Lyra hub** (if `hub.seed` was rotated):

```bash
supervisorctl restart lyra_hub
```

After each restart, wait for the program to reach `RUNNING` state before restarting the next one:

```bash
supervisorctl status
# Confirm the restarted program shows RUNNING before continuing.
```

---

## 6. Verification

**6.1 Check for NATS auth errors** using the reload timestamp captured in Step 4:

```bash
scripts/check-nats-acls.sh --since "${RELOAD_TS}" --window 90 | tee ~/nkey-rotation-evidence.txt
```

Expected output on success: `OK: no Permissions Violation in nats.service over 90s window`

If violations are detected, the script prints the offending lines and exits 1. Jump to **Rollback** immediately.

**6.2 Check each restarted service log for a successful NATS connection:**

```bash
# Hub
tail -30 ~/.local/state/lyra/logs/lyra_hub.log | grep -i "nats\|connected\|ready"

# Telegram adapter
tail -30 ~/.local/state/lyra/logs/lyra_telegram.log | grep -i "nats\|connected\|ready"

# Discord adapter
tail -30 ~/.local/state/lyra/logs/lyra_discord.log | grep -i "nats\|connected\|ready"

# voicecli workers (if rotated)
tail -30 ~/.local/state/voicecli/logs/voicecli_tts.log | grep -i "nats\|connected\|ready"
tail -30 ~/.local/state/voicecli/logs/voicecli_stt.log | grep -i "nats\|connected\|ready"
```

**6.3 Confirm supervisor program states:**

```bash
supervisorctl status
```

All programs should show `RUNNING`. Any program in `FATAL` or `BACKOFF` immediately after restart indicates an auth failure — see Rollback.

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

**7.2 Restore the compromised seed:**

```bash
# Replace BAK_TS with the actual timestamp from your Step 2 backup.
BAK_TS=20240120-1430

cp ~/.lyra/nkeys/${IDENTITY}.seed.bak-${BAK_TS} ~/.lyra/nkeys/${IDENTITY}.seed
chmod 0600 ~/.lyra/nkeys/${IDENTITY}.seed
```

**7.3 Restore auth.conf and reload NATS:**

```bash
# Replace CONF_BAK with the actual backup filename from Step 3 output.
CONF_BAK=/etc/nats/nkeys/auth.conf.bak.20240120-143012

sudo cp -a "${CONF_BAK}" /etc/nats/nkeys/auth.conf
sudo chown root:nats /etc/nats/nkeys/auth.conf
sudo chmod 0640 /etc/nats/nkeys/auth.conf
sudo systemctl reload nats.service
```

**7.4 Reverse-order restart** (workers first, hub last — same order as Step 5):

```bash
supervisorctl restart voicecli_tts voicecli_stt
supervisorctl restart lyra_imagecli_gen
supervisorctl restart lyra_telegram lyra_discord lyra_tts lyra_stt
supervisorctl restart lyra_hub
```

**7.5 Re-run verification** (Step 6) to confirm the rollback restored service. Then escalate: the rotation failed, the compromised seed is live again, and the compromise signal must be reassessed before the next attempt.

---

## 8. Cross-references

- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — declarative provisioning invariants, `--regen-authconf` semantics, `lyra ops verify` (Invariant 5, planned)
- [#561](https://github.com/Roxabi/lyra/issues/561) — parent epic (NATS nkey provisioning)
- [#714](https://github.com/Roxabi/lyra/issues/714) — per-role ACL rework
- [`deploy/nats/gen-nkeys.sh`](../../deploy/nats/gen-nkeys.sh) — seed generation and auth.conf rendering
- [`scripts/check-nats-acls.sh`](../../scripts/check-nats-acls.sh) — ACL violation detector used in Step 6.1
