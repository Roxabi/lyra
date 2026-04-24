---
title: "CDI GPU Passthrough Validation — Rootless Podman on M₁"
status: gate-check
scope: Phase 2 entry (voiceCLI / imageCLI Quadlet migration)
risk-ref: PROD-MIGRATION-STRATEGY.md §5 Risk 5
date: 2026-04-23
---

# CDI GPU Validation Runbook

Target: M₁ (`roxabituwer`, Ubuntu 26.04 LTS, Podman 5.x apt, RTX 3080, rootless + linger)

Pass = GPU visible in rootless Quadlet container, survives reboot.
Fail = policy-exception path (GPU workloads stay as plain systemd services, no container).

---

## 1. Prerequisites Check

Run all as the **rootless user** (not sudo) unless noted.

```bash
# 1a. NVIDIA driver version (must be >= 525 for CDI support)
nvidia-smi --query-gpu=driver_version --format=csv,noheader

# 1b. nvidia-container-toolkit installed and version
dpkg -l nvidia-container-toolkit 2>/dev/null || echo "NOT INSTALLED"
nvidia-ctk --version

# 1c. CDI spec file location
ls -lh /etc/cdi/nvidia.yaml 2>/dev/null || echo "not at /etc/cdi/"
ls -lh /var/run/cdi/nvidia.yaml 2>/dev/null || echo "not at /var/run/cdi/"

# 1d. Podman version and CDI support
podman --version
podman info | grep -i cdi

# 1e. Rootless subuid/subgid mapping
grep "^$(whoami)" /etc/subuid /etc/subgid

# 1f. /dev/nvidia* permissions (user must be in 'video' or 'render' group, or devices accessible)
ls -lh /dev/nvidia* /dev/nvidiactl /dev/nvidia-uvm 2>/dev/null
groups | tr ' ' '\n' | grep -E 'video|render'

# 1g. Linger status
loginctl show-user "$(whoami)" | grep Linger
```

**What to look for:**
| Check | Pass | Fail → action |
|---|---|---|
| Driver version | >= 525 | Update nvidia driver |
| `nvidia-ctk` | present, any version | `sudo apt install nvidia-container-toolkit` |
| CDI spec exists | file present, size > 0 | Run §2 to generate |
| `podman info` CDI | `cdi: true` or `CDISpecDirs` listed | Podman too old (unlikely on 26.04) |
| subuid/subgid | entry present with range >= 65536 | `sudo usermod --add-subuids 100000-165535 $(whoami)` |
| `/dev/nvidia*` | world-readable or user in video/render | `sudo usermod -aG video,render $(whoami)` then re-login |
| Linger | `Linger=yes` | `loginctl enable-linger $(whoami)` |

---

## 2. CDI Spec Generation

**Spec MUST live at `/etc/cdi/nvidia.yaml`** (persistent). Do NOT rely on the toolkit's
post-install auto-generation at `/var/run/cdi/nvidia.yaml` — that path is tmpfs, wiped on
reboot, and depends on a systemd oneshot that can fail silently after a driver update.

**Required one-time setup on a fresh host:**
```bash
sudo mkdir -p /etc/cdi
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
sudo rm -f /var/run/cdi/nvidia.yaml   # prevent dual-spec drift
```

**Required apt hook — auto-regenerate + fail loud *when nvidia changes*:**
```bash
sudo tee /etc/apt/apt.conf.d/99-nvidia-cdi-regenerate >/dev/null <<'HOOK'
DPkg::Post-Invoke { "STAMP=/var/lib/nvidia-cdi.stamp; CUR=$(dpkg-query -W -f='${Package} ${Version}\n' 'nvidia-container-toolkit' 'nvidia-driver-*' 'libnvidia-*' 2>/dev/null | sort | sha1sum | cut -d' ' -f1); [ -f \"$STAMP\" ] && [ \"$(cat $STAMP)\" = \"$CUR\" ] && exit 0; command -v nvidia-ctk >/dev/null || exit 0; echo '[nvidia-cdi] nvidia packages changed — regenerating /etc/cdi/nvidia.yaml'; mkdir -p /etc/cdi; nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml.new && [ -s /etc/cdi/nvidia.yaml.new ] && mv /etc/cdi/nvidia.yaml.new /etc/cdi/nvidia.yaml && echo \"$CUR\" > \"$STAMP\" || { rm -f /etc/cdi/nvidia.yaml.new; echo '[nvidia-cdi] ERROR: CDI regeneration failed after nvidia update'; exit 1; }"; };
HOOK
```
**Intent:** fail loud *only when nvidia actually changed*. A stamp file (`/var/lib/nvidia-cdi.stamp`)
holds the hash of installed nvidia package versions; if unchanged, hook exits 0 and unrelated
apt transactions (e.g. `apt install vim`) are never impacted. On real nvidia updates, the hook
regenerates atomically (`.new` + `mv`) and aborts apt on failure — preserving no-silent-drift.

**Verify spec is valid:**
```bash
nvidia-ctk cdi list
# Expected: nvidia.com/gpu=0, nvidia.com/gpu=<UUID>, nvidia.com/gpu=all
ls -lh /etc/cdi/nvidia.yaml   # must be present, ~20K
```

**Validated (2026-04-24):**
- M₁ (roxabituwer, Ubuntu 26.04, Podman 5.7.0, RTX 3080): §1–§4 pass
- M₂ (ROXABITOWER, Pop!_OS, Podman 4.9.3, RTX 5070 Ti): §1–§3 pass (Quadlet §4 not re-verified here; Podman 4.9 supports `AddDevice=nvidia.com/gpu=all` so expected to pass)

Both hosts have persistent spec at `/etc/cdi/nvidia.yaml` and apt hook installed.

**Podman version parity note:** M₂ runs 4.9.3 (Pop!_OS apt). Some Podman 5.x Quadlet
directives used in production (`UserNS=keep-id:uid=...` per ADR-054) may not exist on 4.9.
Treat M₂ as a partial dry-run env for CDI/GPU only; full Quadlet topology parity happens
on M₁.

---

## 3. Minimal Smoke Test

```bash
podman run --rm \
  --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:12.4.1-base-ubuntu22.04 \
  nvidia-smi
```

**Expected output (pass signature):**
```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 5xx.xx.xx    Driver Version: 5xx.xx.xx    CUDA Version: 12.x   |
|-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC|
|   0  NVIDIA GeForce RTX 3080 ...
```

GPU name `RTX 3080` must appear. Any `Failed to initialize NVML` or `no devices found`
is a hard fail — see §5 for remediation.

**If the image pull is slow, pre-pull separately:**
```bash
podman pull docker.io/nvidia/cuda:12.4.1-base-ubuntu22.04
```

**Alternative tag** (if 12.4.1 is not available on the registry at validation time):
```bash
podman run --rm --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:12.3.2-base-ubuntu22.04 \
  nvidia-smi
```

---

## 4. Quadlet Integration Test

Create the unit file:

```bash
mkdir -p ~/.config/containers/systemd/

cat > ~/.config/containers/systemd/gpu-test.container << 'EOF'
[Unit]
Description=GPU CDI smoke test (Quadlet)
After=default.target

[Container]
Image=docker.io/nvidia/cuda:12.4.1-base-ubuntu22.04
Exec=nvidia-smi
AddDevice=nvidia.com/gpu=all
AutoUpdate=disabled

[Service]
Type=oneshot
RemainAfterExit=no

[Install]
WantedBy=default.target
EOF
```

**Load and run:**
```bash
systemctl --user daemon-reload
systemctl --user start gpu-test.service
systemctl --user status gpu-test.service
journalctl --user -u gpu-test.service --no-pager
```

**Pass:** `journalctl` output contains the `nvidia-smi` table with RTX 3080 and exit code 0.

**Cleanup:**
```bash
rm ~/.config/containers/systemd/gpu-test.container
systemctl --user daemon-reload
```

**Directive note:** `AddDevice=nvidia.com/gpu=all` is the correct Quadlet directive for CDI
devices in Podman 5.x (maps to `podman run --device`). Do NOT use `AddDevice=/dev/nvidia0`
(raw device path) for rootless — CDI handles the full device group and library injection.

---

## 5. Failure Modes + Remediation

### F1: `no-cgroups=true` not set → NVML init failure in rootless

**Symptom:** `Failed to initialize NVML: Unknown Error` inside the container.

**Cause:** nvidia-container-runtime tries to manipulate cgroups, which rootless containers
cannot do without cgroup delegation.

**Fix:**
```bash
# Check current value
grep no-cgroups /etc/nvidia-container-runtime/config.toml 2>/dev/null \
  || grep no-cgroups /etc/nvidia-container-config.toml 2>/dev/null

# Set it (requires sudo)
sudo nvidia-ctk config --set nvidia-container-cli.no-cgroups=true \
  --config /etc/nvidia-container-runtime/config.toml
```

On Ubuntu 26.04 with nvidia-container-toolkit >= 1.14, this flag may be superseded by
CDI mode (CDI does not use the container-cli path at all). If CDI spec generates cleanly
and `nvidia-ctk cdi list` shows the device, try the smoke test first before touching this.

### F2: Stale CDI spec after driver update

**Prevention:** The apt hook in §2 makes this fail loud (apt transaction aborts if regen
fails) — you should never hit this in normal operation. Hook presence:
```bash
ls /etc/apt/apt.conf.d/99-nvidia-cdi-regenerate   # must exist
```

**If the hook is missing or spec was manually tampered:**
```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

**Symptom of stale spec:** `nvidia-smi` inside container shows wrong driver version, or
`Error: CDI device injection failed: could not inject ... library not found`.

### F3: UserNS mapping excludes GPU device major numbers

**Symptom:** `podman run --device nvidia.com/gpu=all` exits with
`Error: OCI runtime error: ... permission denied on /dev/nvidia0` or device is missing
inside container even though CDI spec is valid.

**Cause:** Rootless Podman maps UIDs/GIDs through subuid/subgid. Device nodes owned by
`root:video` (group GID e.g. 44) may fall outside the mapped range, or the user is not
in the `video`/`render` groups.

**Fix:**
```bash
# Add user to video and render groups (requires sudo + re-login)
sudo usermod -aG video,render $(whoami)
# Log out and back in (or: newgrp video in the same shell for testing)

# Verify group membership took effect
id | grep -E 'video|render'

# Re-run smoke test
```

If the device nodes are `crw-rw----` root:video (660), the user must be in the `video`
group. Ubuntu 26.04 ships NVIDIA device nodes this way by default.

---

## 6. Decision Criteria

### PASS — CDI is viable, proceed to Phase 2

All three must be true:
- [ ] §3 smoke test: `nvidia-smi` inside `podman run` shows RTX 3080, exit 0
- [ ] §4 Quadlet test: `systemctl --user start gpu-test.service` exits 0 with GPU visible in journal
- [ ] Reboot test: after `sudo reboot`, re-run §4 Quadlet test with `systemctl --user start gpu-test.service` — GPU still visible (confirms linger + CDI spec survive reboot)

**Reboot test command sequence (post-reboot):**
```bash
# Wait ~30s after reboot for linger session to restore
systemctl --user status  # confirm user session is running
systemctl --user start gpu-test.service
journalctl --user -u gpu-test.service --no-pager | grep -E 'RTX|GeForce|Error'
```

### FAIL — policy-exception path

Triggered by any of:
- `nvidia-smi` inside rootless container returns non-zero or NVML error after §5 remediations exhausted
- Quadlet `AddDevice=nvidia.com/gpu=all` not recognized (Podman version < 4.7 — unlikely on Ubuntu 26.04)
- GPU not visible after reboot (linger CDI path issue with no practical fix)

**Policy exception:** GPU workloads (`voicecli_tts`, `voicecli_stt`, `imagecli_gen`) remain
as plain systemd user services (direct `uv run` command, no container). All other projects
proceed to Quadlet unaffected. Document exception in the cross-project ADR with a re-evaluation
trigger (next major nvidia-container-toolkit release or Ubuntu point update).

---

## Quick Reference

```
nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml   # regenerate (after driver update)
nvidia-ctk cdi list                                       # verify spec
podman info | grep -i cdi                                 # podman CDI enabled?
podman run --rm --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```
