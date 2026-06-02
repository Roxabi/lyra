# V4 Provision Idempotency Runbook

**Slice:** V4 — provision.sh extension
**AC:** ops-#1 — `provision.sh` is idempotent on the `factory-gh-pem` secret bootstrap block.
**Executed on:** M1 (roxabituwer, 192.168.1.16) during slice V4 RED-GATE acceptance.

---

## Scope

Validates that the "Lyra GitHub App PEM (Podman secret)" block added in commit `eab74935` (`deploy/provision.sh` lines 380-400) is idempotent: running `provision.sh` twice in sequence creates the secret exactly once and leaves no state diff.

Does NOT validate the remainder of `provision.sh` (apt packages, ufw, user creation, etc.). Those sections are covered by existing provisioning tests and are orthogonal to AC ops-#1.

---

## 1. Pre-flight

Run these checks before starting. All three must pass.

**1.1 Confirm the worktree is on the correct branch:**

```bash
cd ~/projects/lyra
git branch --show-current
# Expected: feat/1078-gh-app-identity  (or staging once merged)
```

**1.2 Capture a podman secret baseline:**

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret ls \
  | tee /tmp/secret-baseline.txt
# Confirm factory-gh-pem is NOT listed.
```

If `factory-gh-pem` already exists from a prior run, remove it now:

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem
```

**1.3 Generate a test PEM file (content is irrelevant — only the path matters to provision.sh):**

```bash
openssl genrsa -out /tmp/test-lyra-app.pem 2048
ls -la /tmp/test-lyra-app.pem
# Expected: file exists, size ~1.7 KB
```

---

## 2. First run

Ensure `factory-gh-pem` is absent, then run provision.sh with the test PEM.

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem 2>/dev/null || true
GH_PEM_PATH=/tmp/test-lyra-app.pem bash deploy/provision.sh 2>&1 | tee /tmp/provision-run-1.log
```

Capture the secret state immediately after:

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 \
  podman secret inspect factory-gh-pem > /tmp/secret-after-run-1.json
echo "Exit: $?"
```

Note: `provision.sh` runs many sections beyond the secret block (apt updates, ufw rules, etc.). If those sections fail in the test environment, use the isolation strategy in section 7 instead of the full script.

Expected output in `/tmp/provision-run-1.log` for the relevant block:

```
[+] Lyra GitHub App PEM (Podman secret)
[+] Podman secret 'factory-gh-pem' created from /tmp/test-lyra-app.pem.
```

---

## 3. Second run (idempotency check)

Run provision.sh a second time with the same `GH_PEM_PATH`:

```bash
GH_PEM_PATH=/tmp/test-lyra-app.pem bash deploy/provision.sh 2>&1 | tee /tmp/provision-run-2.log
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 \
  podman secret inspect factory-gh-pem > /tmp/secret-after-run-2.json
```

Expected output in `/tmp/provision-run-2.log` for the relevant block:

```
[+] Lyra GitHub App PEM (Podman secret)
[+] Podman secret 'factory-gh-pem' already present, skipping.
```

---

## 4. Verify

All four checks must pass. Record PASS/FAIL for each.

**4.1 Both runs exited 0:**

```bash
grep -c '\[x\]' /tmp/provision-run-1.log /tmp/provision-run-2.log
# Expected: both counts = 0 (no [x] error lines)
tail -5 /tmp/provision-run-1.log
tail -5 /tmp/provision-run-2.log
```

**4.2 Second run logged the skip message:**

```bash
grep 'already present, skipping' /tmp/provision-run-2.log
# Expected: [+] Podman secret 'factory-gh-pem' already present, skipping.
```

**4.3 Secret state is identical between runs (diff must be empty):**

```bash
diff /tmp/secret-after-run-1.json /tmp/secret-after-run-2.json
# Expected: no output (empty diff)
```

Note: `podman secret inspect` includes a `CreatedAt` timestamp but not an `UpdatedAt` field — the diff should be empty because the secret object was not modified on the second run.

**4.4 Exactly one secret entry:**

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret ls | grep factory-gh-pem | wc -l
# Expected: 1
```

---

## 5. Negative tests

Verify that input validation rejects bad inputs. Run each sub-step independently. The `error()` function in provision.sh exits with code 1 and prints `[x] <message>`.

**5.1 Bad path (shell metacharacters) — expect exit 1 + "Invalid GH_PEM_PATH":**

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem 2>/dev/null || true
GH_PEM_PATH='/tmp/foo;rm -rf /' bash deploy/provision.sh 2>&1 | grep -E 'Invalid GH_PEM_PATH|exit'
echo "Exit code: $?"
# Expected: line containing "Invalid GH_PEM_PATH" printed; exit code 1
```

**5.2 Valid path format but file absent — expect exit 1 + "PEM file not found":**

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem 2>/dev/null || true
GH_PEM_PATH=/tmp/does-not-exist.pem bash deploy/provision.sh 2>&1 | grep -E 'PEM file not found|exit'
echo "Exit code: $?"
# Expected: line containing "PEM file not found" printed; exit code 1
```

**5.3 GH_PEM_PATH unset — expect script to continue (non-fatal) with a warning:**

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem 2>/dev/null || true
env -u GH_PEM_PATH bash deploy/provision.sh 2>&1 | grep 'GH_PEM_PATH not set'
# Expected: line containing "GH_PEM_PATH not set — skipping factory-gh-pem bootstrap."
# Script continues and exits 0 (unset is a warn, not an error)
```

---

## 6. Isolation strategy (fallback)

If the full `provision.sh` fails in the test environment due to sections unrelated to the secret block (apt, ufw, network, etc.), run only the secret block in isolation:

```bash
# Extract and execute only the factory-gh-pem section
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem 2>/dev/null || true
GH_PEM_PATH=/tmp/test-lyra-app.pem \
  bash -c "$(awk '/Lyra GitHub App PEM/,/factory-gh-pem.*created.*GH_PEM_PATH/' deploy/provision.sh)"
```

The idempotency check and negative tests in sections 4 and 5 remain unchanged. Record that only the block was run (not full provision.sh) in your acceptance evidence.

---

## 7. Cleanup

Remove all artifacts created during this runbook:

```bash
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret rm factory-gh-pem
rm -f /tmp/test-lyra-app.pem \
      /tmp/provision-run-1.log \
      /tmp/provision-run-2.log \
      /tmp/secret-after-run-1.json \
      /tmp/secret-after-run-2.json \
      /tmp/secret-baseline.txt
sudo -u mickael XDG_RUNTIME_DIR=/run/user/1000 podman secret ls | grep factory-gh-pem \
  && echo "WARNING: secret not removed" || echo "Clean."
```

---

## 8. Cross-references

- **Spec AC ops-#1** — `artifacts/specs/1078-github-app-identity-spec.mdx` line 263: "provision.sh is idempotent: running twice on a clean host produces zero error and zero state diff"
- **Plan T14** — `artifacts/plans/1078-github-app-identity-plan.mdx`: "Verify: diff empty, both runs exit 0"
- **T13 commit** — `eab74935` — "Extend deploy/provision.sh with idempotent factory-gh-pem secret bootstrap"
- **Plan T13** — `artifacts/plans/1078-github-app-identity-plan.mdx`: provision.sh extension (Slice V4, AC ops-#1)
- **Source block** — `deploy/provision.sh` lines 380-400: "Lyra GitHub App PEM (Podman secret)" section
