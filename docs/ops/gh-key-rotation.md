# GitHub App PEM Rotation Runbook

## Scope

This runbook covers rotating the private key (PEM) for the Lyra GitHub App. Rotate when the key is suspected compromised, as part of a scheduled key refresh, or when a new operator takes ownership. The operation replaces the `lyra-gh-pem` Podman secret on the target host and restarts `lyra-clipool` to load the new key; expected downtime is ≤10 s end-to-end.

This runbook does **not** cover changing the GitHub App ID or installation ID — those are properties of the registered App and changing them requires re-registering the App and updating the Quadlet `Environment=` values in `deploy/quadlet/lyra-clipool.container`. It also does not cover first-time bootstrap; for that, see `deploy/provision.sh` section "Lyra GitHub App PEM (Podman secret)".

---

## When to Rotate

- **Compromise suspected** — PEM leaked in logs, visible in a build artifact, exfiltrated from disk, or accessible to an unauthorized party.
- **Scheduled rotation** — recommended cadence: every 90 days for prod (M₁), every 180 days for dev (M₂). These are operational heuristics, not a policy requirement.
- **Operator key change** — the GitHub App "Generate a private key" action on github.com invalidates the previous key; rotation must follow immediately.

---

## Identity → Host Map

| App | Host | Quadlet unit | Secret name | Env vars |
|---|---|---|---|---|
| Lyra-prod | M₁ — roxabituwer (192.168.1.16) | `lyra-clipool.service` | `lyra-gh-pem` | `LYRA_GH_APP_ID` + `LYRA_GH_INSTALLATION_ID` (prod values) |
| Lyra-dev | M₂ — ROXABITOWER | `lyra-clipool.service` | `lyra-gh-pem` | `LYRA_GH_APP_ID` + `LYRA_GH_INSTALLATION_ID` (dev values) |

The secret name `lyra-gh-pem` is identical on both hosts. Each host has an isolated Podman secret store — there is no cross-host conflict.

---

## 1. Pre-flight

**1.1 Generate the new PEM.**
On github.com, navigate to Settings → Developer settings → GitHub Apps → Lyra-prod (or Lyra-dev) → "Private keys" → "Generate a private key". GitHub downloads a `.pem` file automatically. Save it to a local path.

**1.2 Copy the new PEM to the target host.**

For prod (M₁):

```bash
scp ~/Downloads/lyra-prod.private-key.YYYY-MM-DD.pem mickael@192.168.1.16:/tmp/new-lyra-prod.pem
```

For dev (M₂), use the equivalent path on ROXABITOWER — the script runs locally, so no SCP is needed if you are already on M₂.

**1.3 Confirm clipool is currently Up.**

```bash
ssh mickael@192.168.1.16 'systemctl --user is-active lyra-clipool.service'
```

Expected: `active`. If clipool is already down for an unrelated reason, resolve that first — a degraded baseline makes post-rotation verification ambiguous.

**1.4 Capture the rotation start timestamp for the audit trail.**

```bash
RT=$(date -Iseconds)
echo "Rotation start: $RT"
```

---

## 2. Run the Rotation Script

On the target host:

```bash
cd ~/projects/lyra
time bash deploy/scripts/rotate-gh-key.sh /tmp/new-lyra-prod.pem
```

The script:
1. Removes the existing `lyra-gh-pem` Podman secret (tolerates first-time absence).
2. Creates a new `lyra-gh-pem` secret from the supplied PEM path.
3. Runs `systemctl --user restart lyra-clipool`.
4. Polls `podman ps` every 0.5 s for up to 10 s, waiting for the container status to show `Up`.

**Expected output:** `lyra-clipool restarted, secret rotated.`
**Expected wall-clock:** ≤10 s total.

> **Warning:** the restart is a hard stop — in-flight `git push` or `gh` operations using the old token will receive a TCP reset. The script does not drain in-flight operations. If you need to avoid disrupting an active push, wait for it to complete before running the script.

If the script exits non-zero, jump to **[4. Rollback](#4-rollback)**.

---

## 3. Verify

**3.1 Confirm clipool is healthy.**

```bash
systemctl --user is-active lyra-clipool.service
podman ps --filter name=lyra-clipool --format '{{.Status}}'
```

Expected: `active` and a status line beginning with `Up`.

**3.2 Check clipool logs for mint activity.**

```bash
journalctl --user -u lyra-clipool --since "$RT" | grep -iE 'mint|github|token|error'
```

Look for absence of `MintFailure` lines. A successful token mint by the helper process confirms the new PEM was read and accepted by GitHub's API.

**3.3 End-to-end git credential check.**

From inside the clipool container, exercise the transparent git credential helper with the new PEM:

```bash
podman exec -it lyra-clipool su - lyra -c \
  'cd /home/lyra/projects/lyra && git fetch origin staging --dry-run'
```

Expected: `git fetch` completes without auth errors. The credential helper resolves a fresh installation token from the new PEM via the in-container Unix socket.

**3.4 Wipe the staging copy of the PEM from the host.**

```bash
shred -u /tmp/new-lyra-prod.pem
```

---

## 4. Rollback

Trigger rollback when:
- The rotation script exits non-zero.
- `lyra-clipool` fails to return to `Up` state within 10 s.
- `git fetch`/`git push` returns auth errors post-rotation.

**4.1 If you have the previous PEM archived** (recommended: keep the prior rotation's PEM in a sealed location for ≥7 days):

```bash
bash deploy/scripts/rotate-gh-key.sh /path/to/previous-lyra-prod.pem
```

Then re-run the verification steps in Section 3.

**4.2 If you do not have the previous PEM and clipool is offline:**

The bot is in an incident state. Fall back to generating a fresh key on github.com (same steps as Pre-flight 1.1) and rotating immediately with the new key. If the App registration itself is broken, treat this as a full incident — see TODO: incident playbook for Lyra GH identity offline (link pending).

> **Note:** rollback re-activates a potentially compromised key temporarily. Record the rollback in your audit log and schedule a second rotation attempt within 24 h once the root cause is resolved.

---

## 5. Audit Log Entry

Record the rotation in your operations journal:

- **Date + RT timestamp** — captured in Pre-flight 1.4
- **App rotated** — Lyra-prod or Lyra-dev
- **Rotation reason** — scheduled / compromise / operator key change
- **Wall-clock downtime measured** — from `time` output in Step 2
- **Verifier** — who ran Steps 3.1–3.3
- **Disposition of the old PEM** — shredded on host / archived for forensics / still active (rollback case)

---

## 6. Cross-References

- [Plan #1078](../../artifacts/plans/1078-github-app-identity-plan.mdx) — T15 (rotation script) + T24 (this runbook)
- [Spec #1078](../../artifacts/specs/1078-github-app-identity-spec.mdx) — slice V5 (UC6 key rotation), AC ops-#2 (≤10 s downtime)
- [`deploy/scripts/rotate-gh-key.sh`](../../deploy/scripts/rotate-gh-key.sh) — the rotation script
- [`deploy/provision.sh`](../../deploy/provision.sh) — section "Lyra GitHub App PEM (Podman secret)" for first-time bootstrap
- [`docs/ops/nkey-rotation.md`](nkey-rotation.md) — sibling runbook for NATS nkey rotation
