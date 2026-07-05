# nkey Rotation Runbook — Compromise Case

## Scope

This runbook applies **only when a specific nkey seed is suspected compromised** — meaning the seed file's raw private content may have been observed by an unauthorized party (exfiltrated from disk, leaked in logs, captured in a backup, etc.). It replaces the seed(s) with fresh key material.

If you are here because `auth.conf` is out of date, an ACL grant is wrong, or a new identity is missing, **stop** — those are non-destructive routine changes handled by:

- [nats-authconf-update.md](nats-authconf-update.md) — ACL/permission changes (`make nats-regen-authconf`)
- [nats-identity-lifecycle.md](nats-identity-lifecycle.md) — adding (`make nats-add-identity`) or retiring an identity

**If you are not responding to a suspected compromise, you do not want this runbook.**

Rotation replaces the seed file (private key material) for one or more identities. The affected processes authenticate with new credentials after their consuming unit restarts. All other identities keep their existing seeds untouched.

---

## Architecture (what a rotation actually touches)

- **Seeds** live in `~/.roxabi/factory/nkeys/*.seed` on Machine 1 (operator-owned, `0600`), one file per active identity in [`acl-matrix.json`](../../deploy/nats/acl-matrix.json). That JSON is the SSoT for the identity set — do **not** hard-code a count here; read it live.
- **`auth.conf`** (the public ACL bundle: one `users {}` block per active identity, holding only *public* keys) is rendered from the seeds to `~/.roxabi/factory/nkeys/auth.conf` and delivered to the `factory-nats` container as an **inline bind mount** (`ADR-085`), not a Podman secret. nats-server re-reads it on SIGHUP reload or on restart.
- **Private seeds** reach each *consuming* unit as a per-identity Podman secret `factory-nats-<identity>` (`type=mount`, `ADR-054`). A client only sees a new seed after its unit restarts.
- No TLS is in play. Clients (including LAN clients such as `voice-client` on M₂) connect over `nats://…:4222` with NKey auth — there are no certs to check.

### Which unit consumes an identity

The seed for identity `<name>` is mounted into exactly one unit via the Podman secret `factory-nats-<name>`. Find the consuming unit and its log stream:

```bash
# On Machine 1 — which unit mounts this identity's seed?
grep -l "factory-nats-<name>" ~/projects/roxabi-factory/deploy/quadlet/*.container
# e.g. hub → deploy/quadlet/factory-hub.container → factory-hub.service
journalctl --user -u factory-hub
```

The `owner` field of each identity in `acl-matrix.json` says which project owns it: `factory` identities run as `factory-*` units on M₁; `voicecli` identities (`voice-tts`, `voice-stt`, `voice-client`) belong to the voiceCLI project and run as `voicecli-*` units on their host.

---

## 1. Pre-flight

**1.1 Confirm the compromise signal.**
Document what you observed: which seed, when, and how it was exposed. Do not proceed on vague suspicion — rotation is disruptive. The evidence should be concrete (e.g., seed file visible in a public log, backup accessible to the wrong party, file exfiltrated).

**1.2 Identify which identity (or identities) to rotate.**
List the affected seed filenames — e.g. `telegram-adapter.seed`. **If `hub` is compromised, treat all identities as potentially compromised and rotate every seed** (Path B below).

**1.3 Confirm SSH access to Machine 1.**

```bash
ssh mickael@192.168.1.16
```

**1.4 Confirm a baseline before starting.**

`factory ops verify` connects as each identity and checks its live publish ACL (ADR-046 invariant 5):

```bash
# On Machine 1:
factory ops verify
```

To inspect the current rendered `auth.conf` (rootless — reads the operator-owned copy):

```bash
# On Machine 1:
factory-acl genkeys --show
# The count of `users {}` blocks equals the active identities in acl-matrix.json.

systemctl --user status 'factory-*.service'
# All units should be active (running) before you begin.
```

If any unit is already in a failed state unrelated to this rotation, resolve it first — a degraded baseline makes verification ambiguous.

---

## Path A — rotate one (or a few) identities

Use this when specific non-`hub` identities are compromised. Repeat the per-identity steps for each affected identity; substitute its name for `<name>`.

**A.1 Back up the compromised seed** (forensic reference — never re-used to authenticate):

```bash
# On Machine 1 — run once per identity being rotated.
IDENTITY=telegram-adapter
TS=$(date +%Y%m%d-%H%M%S)
cp ~/.roxabi/factory/nkeys/${IDENTITY}.seed ~/.roxabi/factory/nkeys/${IDENTITY}.seed.bak-${TS}
chmod 0600 ~/.roxabi/factory/nkeys/${IDENTITY}.seed.bak-${TS}
```

**A.2 Delete the compromised seed, then regenerate it.**

Deleting the seed first is required: `make nats-add-identity` (which wraps `factory-acl genkeys --add-identity`) only generates fresh key material when the seed file is **absent** — if the seed is present it re-renders `auth.conf` but keeps the existing key.

```bash
cd ~/projects/roxabi-factory
rm ~/.roxabi/factory/nkeys/${IDENTITY}.seed
make nats-add-identity NAME=${IDENTITY}
```

`make nats-add-identity` (single rootless verb, no `sudo`):
- generates a fresh seed for the now-absent identity (`STATE=added`),
- re-renders `auth.conf` from **all** active seeds — the other identities keep their keys,
- recreates the Podman secret `factory-nats-${IDENTITY}` from the new seed,
- **automatically logs the rotation** to `rotation-log.md` (see "Bootstrap grace" note below),
- `systemctl --user reload factory-nats` — nats-server re-reads `auth.conf`; the old public key is gone, so the compromised connection is dropped on the next auth check.

Expected output: `factory-acl: STATE=added` followed by `reloaded factory-nats (SIGHUP)`.

**A.3 Restart the consuming unit** so it loads the new seed from its refreshed secret. Identify the unit per _"Which unit consumes an identity"_ above:

```bash
RELOAD_TS=$(date -Iseconds)   # capture for verification (Step: Verification)
systemctl --user restart factory-telegram.service
systemctl --user status factory-telegram.service   # confirm active (running)
```

Go to **Verification**.

---

## Path B — hub compromised: rotate every seed

`hub` sits at the centre of every request/reply flow; a compromised `hub` seed means all traffic is exposed. Rotate the whole set.

**B.1 Back up the entire nkeys directory:**

```bash
TS=$(date +%Y%m%d-%H%M%S)
cp -a ~/.roxabi/factory/nkeys ~/.roxabi/factory/nkeys.bak-${TS}
```

**B.2 Regenerate all seeds and re-render `auth.conf`:**

```bash
cd ~/projects/roxabi-factory
factory-acl genkeys        # default full-provision: fresh seed for every active identity
```

The command automatically logs each identity's rotation to `rotation-log.md` (see "Bootstrap grace" note below).

> If any identity has `deploy.type=external` (currently `voice-client` → M₂), this exits `2` and prints an `scp` manifest on stderr — the seeds are written locally but must be copied to the remote host before the fleet is consistent. Copy them, then re-run with `--ack-external-distribution`. See [nats-authconf-update.md](nats-authconf-update.md) § External seed distribution.

**B.3 Refresh every Podman seed secret** from the new seed files:

```bash
make quadlet-secrets-install
```

**B.4 Restart NATS** to load the new `auth.conf` and evict all existing connections:

```bash
RELOAD_TS=$(date -Iseconds)   # capture for verification
systemctl --user restart factory-nats.service
systemctl --user is-active --wait factory-nats.service
```

**B.5 Rolling restart of every client** — order below (Rolling restart order). Go to **Verification** after.

---

## Rolling restart order

When more than one client must restart (Path B, or Path A touching several identities), restart in this order: **workers first, adapters second, hub last.** Workers and adapters are reconnect-tolerant (circuit breaker in roxabi-nats) and can queue at NATS while the hub is briefly down; the hub is the sole consumer of inbound queues, so restarting it last minimises the window where inbound messages have no consumer.

Restart only units whose identity was rotated. Confirm each reaches `active (running)` before the next.

```bash
# workers (factory + voiceCLI project units, on their host)
systemctl --user restart factory-clipool.service
systemctl --user restart voicecli-tts.service voicecli-stt.service   # voiceCLI project

# adapters
systemctl --user restart factory-telegram.service factory-discord.service

# hub last
systemctl --user restart factory-hub.service

systemctl --user status 'factory-*.service'
```

---

## Verification

**V.1 Check for NATS auth errors** since the restart timestamp captured above:

```bash
tools/check-nats-acls.sh --since "${RELOAD_TS}" --window 90 | tee ~/nkey-rotation-evidence.txt
```

Expected on success: `OK: no Permissions Violation in factory-nats.service over 90s window`. If violations are detected, the script prints the offending lines and exits 1 — go to **Rollback** immediately.

**V.2 Confirm the rotated identity authenticates** against the live server:

```bash
factory ops verify --only ${IDENTITY}     # or run bare `factory ops verify` for all
```

**V.3 Check each restarted unit's log for a clean NATS connection:**

```bash
journalctl --user -u factory-hub      --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"
journalctl --user -u factory-telegram --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"
journalctl --user -u factory-discord  --since "5 min ago" | grep -i "nats\|connected\|ready\|auth\|error"
```

**V.4 Confirm unit states:**

```bash
systemctl --user status 'factory-*.service'
```

Any unit in `failed` state immediately after restart indicates an auth failure — see **Rollback**.

**V.5 Send a test message end-to-end** through Telegram or Discord and confirm a reply arrives. This exercises the full hub → adapter round-trip with the new credentials.

**V.6 Verify the new seed and permissions:**

```bash
ls -la ~/.roxabi/factory/nkeys/ | grep "${IDENTITY}"
# Should show 0600 permissions, owner mickael, with no .bak-* file acting as the active seed.
```

---

## Bootstrap grace — rotation log and seed age policy

When `make nats-add-identity` (Path A) or bare `factory-acl genkeys` (Path B) runs, each identity's rotation is **automatically recorded** to `~/.roxabi/factory/rotation-log.md`. This log feeds a scheduled age-check (`make check-seed-age`, running daily via systemd timer on M₁) that warns at ≥75 days and fails at ≥90 days.

**Bootstrap grace applies:** identities with **no entry** in the rotation log are treated as **always OK** — the check never warns or fails them, regardless of their seed file's age or modification time. This is intentional:

- Syncthing, rsync, and filesystem restores silently reset file modification times, making them an **unreliable age source** for credentials.
- On the day this feature rolls out, all active identities will have zero rotation-log entries yet carry seeds whose actual creation dates already approach or exceed the 75-day warning threshold — a mtime-based check would produce a fleet-wide false-alarm storm on day one, contrary to the feature's intent.

**Seed file mtime is surfaced only as informational.** When the rotation log has no entry for an identity, the checker prints the seed's mtime-derived age as a `NOTE:` (e.g., "seed mtime is 75d old — informational, not policy-enforced"), but this hint never gates the pass/fail verdict. The sole, authoritative age source is the rotation log.

**Fail-open direction, by design.** Every gap in the logging path resolves toward *more* bootstrap grace, never toward a false FAIL:

- The rotation-log append (`rotation_log_append`, via `deploy/lib/operator-log.sh`) is fail-soft — a failed write is swallowed (`|| true` in the shell, `check=False` + a hard timeout on the Python subprocess bridge in `src/factory/operator_audit.py`). If it silently fails for an identity, that identity simply stays in bootstrap grace (no gating verdict) rather than surfacing a "logging is broken" signal. There is currently no reconciliation/alert for this case — an operator auditing rotation coverage should not rely solely on `check-seed-age` being green; cross-check that every active identity in `acl-matrix.json` actually has a `rotation-log.md` entry.
- `factory-acl genkeys` (full provision) buffers each identity's rotation-log entry and only appends it after **every** identity's seed and `auth.conf` are durably written — a mid-run failure (e.g. seed #7 of 18) writes **zero** log entries for that run, so the log can never claim a rotation happened for a run that didn't complete. The tradeoff: if you interrupt a full provision partway through and do *not* retry it, none of that run's identities get a rotation-log entry until you re-run `factory-acl genkeys` to completion — they read as bootstrap-grace, not as falsely fresh. Note: `--regenerate`'s backup/restore path does **not** currently guarantee an automatic seed rollback on a genuine mid-loop failure (a pre-existing gap, predating this feature; no tracking issue filed yet) — a partial failure can leave `nkeys/` in a mixed state requiring manual recovery from the `nkeys.bak.<epoch>` backup that `_backup_seeds()` creates. Either way the rotation-log invariant above holds: whatever state the seeds end up in, the log never claims a rotation that didn't durably complete. Re-run to completion (`--regen-authconf` won't help here — it only re-derives `auth.conf`, it never logs).
- `check_seed_age.py` only swallows a *missing* `rotation-log.md`/seed file (`FileNotFoundError`) as bootstrap grace. A genuine read failure (permissions, disk I/O) on either file propagates as an uncaught error instead of silently returning an all-clear — a broken check must be loud, not green.

**`factory-check-seed-age.service` failing looks like `degraded`.** The unit is `Type=oneshot` with no `RemainAfterExit=`; a real stale-seed FAIL leaves it in `failed` state until the next daily timer run, which folds into `systemctl --user is-system-running` -> `degraded` on Machine 1. This is the intended escalation signal for a genuine credential-age violation — don't mistake it for unrelated drift during a future M1 triage; check `systemctl --user status factory-check-seed-age.service` first when `is-system-running` reports `degraded`.

---

## Rollback

**When to trigger:** any unit in `failed`/backoff after restart, `check-nats-acls.sh` exits 1, auth errors in logs, or the end-to-end test fails.

Rollback restores the pre-rotation seed and re-renders `auth.conf` from it, so the old credentials work again. This **re-activates the compromised seed** — treat it as an incident escalation path, not a routine step.

> **WARNING:** The compromised seed becomes live again the moment `factory-nats` restarts in R.3. Before proceeding: (a) record the time and reason for rollback in your incident log; (b) treat this as temporary — a second rotation must follow within 24 h once the cause of the first failure is understood.

**R.1 Identify the backups:**

```bash
ls ~/.roxabi/factory/nkeys/*.bak-*        # Path A per-seed backup (Step A.1)
ls -d ~/.roxabi/factory/nkeys.bak-*       # Path B whole-dir backup (Step B.1)
```

**R.2 Restore the seed(s):**

```bash
# Path A — single identity. Replace BAK_TS with the suffix from Step A.1 (YYYYMMDD-HHMMSS).
BAK_TS=YYYYMMDD-HHMMSS
cp ~/.roxabi/factory/nkeys/${IDENTITY}.seed.bak-${BAK_TS} ~/.roxabi/factory/nkeys/${IDENTITY}.seed
chmod 0600 ~/.roxabi/factory/nkeys/${IDENTITY}.seed

# Path B — full restore. Replace TS with the suffix from Step B.1.
# cp -a ~/.roxabi/factory/nkeys.bak-${TS}/. ~/.roxabi/factory/nkeys/
```

**R.3 Re-render `auth.conf` from the restored seeds, refresh secrets, restart NATS:**

```bash
cd ~/projects/roxabi-factory
factory-acl genkeys --regen-authconf     # re-derives auth.conf from the seeds now on disk
make quadlet-secrets-install             # refresh the Podman seed secret(s)
systemctl --user restart factory-nats.service
systemctl --user is-active --wait factory-nats.service
```

**R.4 Restart the affected client(s)** in the same order as **Rolling restart order**, confirming each reaches `active (running)`.

**R.5 Re-run Verification** to confirm rollback restored service, then escalate: the rotation failed, the compromised seed is live again, and the compromise signal must be reassessed before the next attempt.

---

## Backup cleanup

After Verification passes, dispose of the seed backups — compromised key material must not linger in the live-seed directory, where it is a leak vector if the directory is later exposed.

```bash
# delete
rm ~/.roxabi/factory/nkeys/${IDENTITY}.seed.bak-${TS}
rm -rf ~/.roxabi/factory/nkeys.bak-${TS}

# — or — move to a forensics archive for incident investigation
mkdir -p ~/.roxabi/factory/forensics
mv ~/.roxabi/factory/nkeys/${IDENTITY}.seed.bak-${TS} ~/.roxabi/factory/forensics/
```

Confirm no `.bak-*` seed remains in the live directory:

```bash
ls ~/.roxabi/factory/nkeys/*.bak-* 2>/dev/null && echo "WARNING: backup files still present"
```

---

## Cross-references

- [`deploy/nats/acl-matrix.json`](../../deploy/nats/acl-matrix.json) — identity registry (SSoT for the active set)
- [nats-authconf-update.md](nats-authconf-update.md) — routine ACL/permission changes; external seed distribution
- [nats-identity-lifecycle.md](nats-identity-lifecycle.md) — adding / retiring identities (`make nats-add-identity`)
- [`tools/check-nats-acls.sh`](../../tools/check-nats-acls.sh) — ACL violation detector used in Verification
- [ADR-046](../architecture/adr/046-nkey-provisioning-declarative-authconf.mdx) — declarative provisioning invariants, `--regen-authconf` semantics, `factory ops verify`
- [ADR-085](../architecture/adr/archive/085-public-aclbundle-bindmount-sighup.mdx) — `auth.conf` as inline bind mount + SIGHUP reload (why it is no longer a Podman secret)
