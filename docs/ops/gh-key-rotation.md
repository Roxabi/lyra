# GitHub App PEM Rotation Runbook

## Scope

This runbook covers rotating the private key (PEM) for the Lyra GitHub App. Rotate when the key is suspected compromised, as part of a scheduled key refresh, or when a new operator takes ownership. The operation replaces the `lyra-gh-pem` Podman secret on the target host and restarts `lyra-gh-helper` (the only container that mounts the PEM in the sidecar Pod design) to load the new key; expected downtime is ≤10 s end-to-end. Measured 0.58 s on M₁ (Podman 5.7.0) on 2026-05-06.

This runbook does **not** cover changing the GitHub App ID or installation ID — those are properties of the registered App and changing them requires re-registering the App and updating the `Environment=` values in `deploy/quadlet/lyra-gh-helper.container`. It also does not cover first-time bootstrap; for that, see `deploy/provision.sh` section "Lyra GitHub App PEM (Podman secret)".

---

## When to Rotate

- **Compromise suspected** — PEM leaked in logs, visible in a build artifact, exfiltrated from disk, or accessible to an unauthorized party.
- **Scheduled rotation** — recommended cadence: every 90 days for prod (M₁), every 180 days for dev (M₂). These are operational heuristics, not a policy requirement.
- **Operator key change** — the GitHub App "Generate a private key" action on github.com invalidates the previous key; rotation must follow immediately.

---

## Identity → Host Map

A single GitHub App `lyra-harness` (id `3619198`, install_id `129952244`) is used on both hosts post the 2026-05-06 single-App collapse. Per-host audit-log separation is deferred until a 2nd contributor or compliance requirement surfaces — see `artifacts/audits/1078-token-isolation-audit.mdx` § Decision Log.

| App | Host | Quadlet unit (PEM consumer) | Secret name |
|---|---|---|---|
| `lyra-harness` | M₁ — roxabituwer (192.168.1.16) | `lyra-gh-helper.service` | `lyra-gh-pem` |
| `lyra-harness` | M₂ — ROXABITOWER | `lyra-gh-helper.service` | `lyra-gh-pem` |

App ID + install_id are git-versioned in `deploy/quadlet/lyra-gh-helper.container` and identical on both hosts. The secret name `lyra-gh-pem` is identical on both hosts. Each host has an isolated Podman secret store — there is no cross-host conflict, and a single PEM file may be re-used (or a per-host PEM, at the operator's discretion).

---

## 1. Pre-flight

**1.1 Generate the new PEM.**
On github.com, navigate to Settings → Developer settings → GitHub Apps → Lyra-prod (or Lyra-dev) → "Private keys" → "Generate a private key". GitHub downloads a `.pem` file automatically. Save it to a local path.

**1.2 Copy the new PEM to the target host.**

For M₁:

```bash
scp ~/Downloads/lyra-harness.private-key.YYYY-MM-DD.pem mickael@192.168.1.16:/home/lyra/secrets/new-lyra-harness.pem
```

For M₂, use the equivalent path on ROXABITOWER — the script runs locally, so no SCP is needed if you are already on M₂.

**1.3 Confirm the pod is currently Up.**

```bash
ssh mickael@192.168.1.16 \
  'systemctl --user is-active lyra-gh-pod.service lyra-gh-helper.service lyra-clipool.service'
```

Expected: `active` for all three. If any is down for an unrelated reason, resolve that first — a degraded baseline makes post-rotation verification ambiguous.

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
time bash deploy/scripts/rotate-gh-key.sh /home/lyra/secrets/new-lyra-harness.pem
```

The script:
1. Removes the existing `lyra-gh-pem` Podman secret (tolerates first-time absence).
2. Creates a new `lyra-gh-pem` secret from the supplied PEM path.
3. Runs `systemctl --user restart lyra-gh-helper.service`.
4. Polls every 0.5 s for up to 10 s, waiting for two conditions: helper container `Up` AND the dispenser socket reachable from inside `lyra-clipool` (`test -S /run/lyra-gh-token/dispenser.sock`).

**Expected output:** `lyra-gh-helper restarted, dispenser reachable, secret rotated.`
**Expected wall-clock:** ≤10 s total. Reference measurement: 0.58 s on M₁ (Podman 5.7.0) 2026-05-06.

> **Warning:** the helper restart is a hard stop — in-flight `git push` or `gh` operations that already hold a minted token continue uninterrupted, but new token requests from `lyra-clipool` during the helper restart window will fail with `ECONNREFUSED` on the dispenser socket. The script does not drain in-flight operations. If you need to avoid disrupting an active operation, wait for it to complete before running the script.

If the script exits non-zero, jump to **[4. Rollback](#4-rollback)**.

---

## 3. Verify

**3.1 Confirm helper + clipool are healthy.**

```bash
systemctl --user is-active lyra-gh-helper.service lyra-clipool.service
podman ps --filter name=lyra-gh-helper --filter name=lyra-clipool --format '{{.Names}} {{.Status}}'
```

Expected: `active active` and two status lines beginning with `Up`.

**3.2 Check helper logs for mint activity.**

```bash
journalctl --user -u lyra-gh-helper --since "$RT" | grep -iE 'mint|github|token|error'
```

Look for absence of `MintFailure` lines. A successful token mint by the helper confirms the new PEM was read and accepted by GitHub's API.

**3.3 End-to-end credential check.**

From inside the clipool container, exercise the dispenser path with the new PEM:

```bash
podman exec lyra-clipool lyra-gh issue list --repo Roxabi/lyra --limit 1
```

Expected: a real issue line is printed. The `lyra-gh` shim resolves a fresh installation token from the new PEM via the dispenser socket and runs `gh` with `GH_TOKEN` scoped to the single subprocess invocation.

**3.4 Wipe the staging copy of the PEM from the host.**

```bash
shred -u /home/lyra/secrets/new-lyra-harness.pem
```

---

## 4. Rollback

Trigger rollback when:
- The rotation script exits non-zero.
- `lyra-clipool` fails to return to `Up` state within 10 s.
- `git fetch`/`git push` returns auth errors post-rotation.

**4.1 If you have the previous PEM archived** (recommended: keep the prior rotation's PEM in a sealed location for ≥7 days):

```bash
bash deploy/scripts/rotate-gh-key.sh /path/to/previous-lyra-harness.pem
```

Then re-run the verification steps in Section 3.

**4.2 If you do not have the previous PEM and clipool is offline:**

The bot is in an incident state. Fall back to generating a fresh key on github.com (same steps as Pre-flight 1.1) and rotating immediately with the new key. If the App registration itself is broken, treat this as a full incident — see TODO: incident playbook for Lyra GH identity offline (link pending).

> **Note:** rollback re-activates a potentially compromised key temporarily. Record the rollback in your audit log and schedule a second rotation attempt within 24 h once the root cause is resolved.

---

## 5. Audit Log Entry

Record the rotation in your operations journal:

- **Date + RT timestamp** — captured in Pre-flight 1.4
- **Host rotated** — M₁ or M₂ (App is `lyra-harness` on both)
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
- [`docs/ops/clipool-git.md`](clipool-git.md) — clipool git behavior reference (SSH rewrite, identity, safe.directory)
- [`docs/ops/nkey-rotation.md`](nkey-rotation.md) — sibling runbook for NATS nkey rotation
