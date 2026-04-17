# Spec — `roxabi-autodeploy`

> Per-project deploy manifests + generic runner, replacing hardcoded `lyra/scripts/deploy.sh`.
> Draft 2026-04-13 · Author: Mickael + Claude · Status: **for review** (architect + devops)

---

## 1. Context

Production (`roxabituwer`) runs a systemd user timer (`lyra-deploy.timer`, 60s) that invokes `~/projects/lyra/scripts/deploy.sh`. The script hardcodes repo paths, program names, branches, test gates, and restart sequences for **lyra** and **voiceCLI**. A pull-only loop for `roxabi-*` was added 2026-04-13 (commit TBD) but the restart logic remained hardcoded.

### Why the current design won't scale

- **Hardcoded per-repo flags** (`LYRA_UPDATED`, `VOICE_UPDATED`) — each new repo = new flag + new branch.
- **Hardcoded program names** drift from `conf.d/*.conf` (see devops review M8 — two supervisor trees).
- **Known bug** (devops M16): `timeout 30 git fetch 2>&1 | tee -a "$LOG_FILE"` — on timeout, `timeout` exits 124, `set -euo pipefail` doesn't catch it because the pipe's exit status is `tee`'s. Script proceeds with stale ref.
- **Lyra-nats-truth trajectory** (M1–M3) adds 3+ supervisor programs (`lyra_cli`, `lyra_llm`, `lyra_harness`) + Quadlet units + eventual zero-downtime hub deploys. Each addition today means editing `deploy.sh`.
- **Per-project concerns leak into lyra**: venv re-sync for voiceCLI, pytest for lyra, pull-only for roxabi-* — all live in one file in the wrong repo.

## 2. Goals

- **G1** Each project owns its deploy behavior via a local manifest.
- **G2** A single generic runner discovers manifests, applies changes, restarts the right services.
- **G3** Preserve current guarantees: test gate, SHA blacklist, hub-first readiness, cross-project venv re-sync.
- **G4** Fix M16 (explicit exit-status checks on `timeout` + `git`).
- **G5** Decouple from supervisor specifics — ready to absorb systemd units / Quadlet later.
- **G6** Zero central edit when adding a new auto-deployed project.

## 3. Non-Goals

- Replacing supervisord as process manager (stays; Quadlet adoption happens per-service, orthogonal).
- Webhook / push-based deploys (stays pull, 60s timer).
- Multi-machine orchestration (single prod machine for now).
- Zero-downtime hub deploys (future M3; spec leaves room but doesn't implement).

## 4. Design

### 4.1 Per-project manifest

Location: `<repo>/deploy/auto-deploy.yml`

```yaml
schema: 1
branch: staging                 # required
pre_restart:                    # optional; run inside the repo after pull
  - sh: "uv sync --all-extras --frozen"
    timeout: 60
  - sh: "uv run pytest --tb=short -q"
    timeout: 120
    on_fail: rollback           # rollback | abort | continue
services:                       # optional; restarted in declared order after all pre_restart steps succeed
  - name: lyra_hub
    type: supervisor            # supervisor | systemd | quadlet (v2)
    gate:                       # optional; wait condition after restart
      kind: state
      value: RUNNING
      stabilize: 2s             # re-check after N seconds (flap filter)
      timeout: 60
  - name: lyra_telegram
  - name: lyra_discord
triggers:                       # optional; run in another repo after successful deploy here
  - in: ~/projects/lyra
    sh: "uv sync --all-extras --upgrade-package voicecli"
    restart: [lyra_hub, lyra_telegram, lyra_discord]
```

> **Runner-level policy, not manifest.** Rollback strategy (`reset_hard`) and blacklist location (`~/.local/state/roxabi/autodeploy/blacklist.txt`) are owned by the runner, not per-project. Manifests only declare `on_fail:` per pre_restart step. This prevents each project re-inventing rollback semantics.

**Minimal manifest** (pull-only, no services — today's `roxabi-*` behavior):

```yaml
schema: 1
branch: staging
```

**Missing manifest** = repo is not auto-deployed (explicit opt-in).

### 4.2 Runner

Path: **`~/projects/roxabi-ops/bin/autodeploy`** (new thin repo — see §5).

Language: **Python 3** + `ruamel.yaml` or `tomli` (pyproject). Rationale: testable, typed error handling, no `yq` dep, reusable modules. Bash considered and rejected — the logic (manifests, gates, triggers, rollback) is beyond bash's sweet spot.

Main loop:

```
for each repo in glob(~/projects/*) with deploy/auto-deploy.yml:
    fetch_with_explicit_status(repo, branch)      # fixes M16
    if local_sha == remote_sha: continue
    if remote_sha in blacklist: continue

    with rollback_guard(repo, strategy=manifest.rollback):
        git pull --ff-only
        for step in manifest.pre_restart:
            run(step) or handle_on_fail(step)

    # restart queue accumulated across all repos, applied after all pulls
    enqueue_restarts(manifest.services)
    enqueue_triggers(manifest.triggers)

apply_restart_queue()   # honors declared service order + gates
apply_triggers()
verify_all_services()
log_summary()
```

### 4.3 Service abstraction

```
class Service:
    name: str
    type: Literal["supervisor", "systemd", "quadlet"]
    managed: bool = True           # False = observe-only; never restart
    gate: Optional[Gate]

    def restart(self) -> None: ...
    def status(self) -> State: ...
```

v1 implements `supervisor` only (via `supervisorctl.sh`). v2 adds `systemd` (for quadlet-generated units) and explicit `quadlet` (for container restart semantics).

**`managed: false`** — for services the runner must *observe* (gate checks, verify sweep) but never restart. NATS Quadlet is the canonical case (`Restart=always` — systemd owns its lifecycle). A `managed: false` entry in `services:` still participates in the post-restart verify pass but is skipped by the restart queue.

**`verify_all_services()` — explicit definition.** After the restart queue drains, scan all services that were restarted this cycle (not just hub; all of them, including trigger-restarted) via `supervisorctl status`. Fail the cycle with non-zero exit if any are in `FATAL` or `BACKOFF`. Post-restart FATAL is **not** rolled back automatically — it's a separate alarm state requiring human attention (rollback is pre-restart only).

### 4.4 Ordering

- **Within a repo**: services restart in declared order; each waits for its `gate` before the next.
- **Across repos**: two-phase commit — **all pulls first**, then **all restarts**. Prevents a failed pull mid-deploy from leaving services restarted against old code.
- **Cross-repo triggers** (e.g. voiceCLI → lyra venv re-sync) run after the originating repo's services are healthy. Services in `trigger.restart` are appended to the restart queue.
- **Dedup rule, precisely**: a service is deduplicated only if its prior restart this cycle was **not preceded by a venv-altering command** (`uv sync`, `uv pip install`, …) that ran after the prior restart. When a trigger executes `uv sync --upgrade-package voicecli` against lyra's venv, the subsequent `lyra_hub` restart is **not** a duplicate — it runs against the new venv.
- **Failed-repo suppression**: if repo A's deploy fails this cycle, every trigger whose `in:` field resolves to A is dropped — never re-sync into a broken state.

### 4.5 Hub-first readiness — generalized

Current code has a bespoke "restart hub, wait for RUNNING + 2s stabilize, then adapters" loop. The spec absorbs this as a **standard `gate`**:

```yaml
services:
  - name: lyra_hub
    gate: {kind: state, value: RUNNING, stabilize: 2s, timeout: 60}
  - name: lyra_telegram
  - name: lyra_discord
```

No special-case code. Same mechanism serves any future ordering need (e.g. `lyra_llm` → `lyra_harness` after M1).

**Reserved for M3: `strategy:` field.** Schema v1 accepts `strategy: sequential` on service entries (default, no-op — just documents intent). Schema v2 adds `strategy: rolling` for M3's two-hub zero-downtime deploy (`lyra_hub_a` → wait healthy → `lyra_hub_b`). Reserving the field now means M3 rollout needs no manifest schema break.

### 4.6 Discovery & conf.d mapping

- Manifest's `services[*].name` is authoritative. Runner does NOT parse `conf.d/*.conf` — manifest is the contract.
- Cross-check at runtime: if `supervisorctl status <name>` returns "no such program", fail loud with a helpful message ("is `<name>` in `conf.d/`?"). Prevents silent drift.

### 4.7 M16 fix — explicit exit status + streaming output

```python
# Bad (current):
#   timeout 30 git fetch origin $BRANCH 2>&1 | tee -a $LOG
# tee's exit status masks timeout's 124.
#
# Good — stream directly to log file handle (no capture_output buffer):
with open(LOG_PATH, "ab") as log_fh:
    result = subprocess.run(
        ["timeout", "30", "git", "fetch", "origin", branch],
        stdout=log_fh, stderr=log_fh,
    )
if result.returncode != 0:
    raise DeployFetchFailed(repo, branch, result.returncode)
```

Applied to every `git` + `uv` + `pytest` invocation. **No `capture_output=True`** on any long-running subprocess — it buffers stdout in RAM until exit, which (a) hides progress during a slow `uv sync` and (b) grows unbounded on verbose git fetches. Streaming to the log file handle keeps output real-time and observable during the cycle.

### 4.8 Logging

Single log at `~/.local/state/roxabi/autodeploy/deploy.log`, rotated weekly (`logrotate.d`). Per-repo sub-logs optional; can defer.

**One structured summary line per cycle, always** — including no-op cycles. Silence is indistinguishable from a timer that stopped firing; a `cycle=noop` line every 60s is the heartbeat ops needs.

```
2026-04-13T09:17:23+02:00 cycle=ok    updated=[lyra@a1b2c3d,voiceCLI@e4f5g6h] restarted=[lyra_hub,lyra_telegram,lyra_discord,voicecli_tts,voicecli_stt] duration=23.4s
2026-04-13T09:18:23+02:00 cycle=noop  updated=[] restarted=[] duration=0.3s
2026-04-13T09:19:23+02:00 cycle=fail  repo=lyra stage=pre_restart step="uv run pytest" exit=1 duration=12.1s
```

### 4.9 Systemd integration

- Rename timer: `lyra-deploy.timer` → **`roxabi-autodeploy.timer`**
- Service: `roxabi-autodeploy.service` → `ExecStart=%h/projects/roxabi-ops/bin/autodeploy`
- Cadence unchanged: `OnBootSec=30`, `OnUnitActiveSec=60`, `AccuracySec=10`

### 4.10 Self-deploy (chicken-and-egg)

```yaml
# roxabi-ops/deploy/auto-deploy.yml
schema: 1
branch: staging
pre_restart:
  - sh: "uv sync --frozen"
    timeout: 60
    on_fail: abort            # NOT rollback — see below
# No services: section — the runner is the "service"; systemd re-execs on next tick.
```

Runner detects self-updates specially: if `roxabi-ops` updated, finish current cycle with the old binary and exit. Systemd `Type=oneshot` re-execs the new binary on the next timer tick. Runner never calls `systemctl daemon-reload`.

**Why `on_fail: abort` (not `rollback`):** if `roxabi-ops`'s own `uv sync` fails mid-cycle, a `rollback` would reset the ops venv back to a working state — hiding the fact that a bad commit was published. `abort` leaves the venv broken, the blacklist records the SHA, and the next tick tries again with the same broken SHA until a new commit lands. Visible failure > silent reset for the tool that deploys everything else.

### 4.11 CLI

```
autodeploy                      # run one cycle (the normal timer entry point)
autodeploy --dry-run            # plan the cycle; print repos-to-pull, steps, services-to-restart, triggers; exit 0; no side effects
autodeploy --only lyra          # restrict cycle to one repo (debug)
autodeploy --verify             # re-run verify_all_services() only; useful for health checks
```

`--dry-run` is **required** for P1 acceptance — migration validation depends on byte-for-byte parity between old `deploy.sh` behavior and the runner's planned actions before we flip the timer.

## 5. Where does the runner live?

Three options evaluated:

| Option | Pro | Con |
|---|---|---|
| **A. `~/projects/lyra/scripts/` (today)** | No new repo | Couples lyra to every project's deploy; this is exactly the problem we're solving |
| **B. `~/projects/roxabi-production/`** | Exists already | Wrong purpose (video rendering / showcase stack, not ops) |
| **C. New `~/projects/roxabi-ops/` (recommended)** | Clean home for cross-project ops; future: backup timers, health probes, SSL renewal | Small infra cost (new repo + GH repo + CI) |

**Recommendation: Option C.** The autodeploy runner is the first tenant; future ops tooling (SQLite backup — devops C2, health probes — C10/C11, etc.) joins it.

## 6. Migration plan

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **P1** | `roxabi-ops` repo created; runner v1 (Python) with supervisor service type; lyra manifest at parity with current `deploy.sh` | Dry-run on prod matches current behavior for lyra |
| **P2** | voiceCLI manifest + cross-repo trigger into lyra venv; delete voiceCLI block from `deploy.sh` | voiceCLI → lyra re-sync works end-to-end |
| **P3** | Flip timer: `lyra-deploy.timer` disabled, `roxabi-autodeploy.timer` enabled | One full week green on prod |
| **P4** | Delete `lyra/scripts/deploy.sh`; roxabi-* manifests added per repo (minimal `branch:` only) | `scripts/deploy.sh` is gone |
| **P5 (future)** | `systemd` service type for Quadlet units (aligns with M0–M3 lyra-nats-truth direction) | `lyra_nats` (Quadlet) restart works via manifest |

Rollback for each phase: revert the timer, keep the old `deploy.sh` untouched through P3.

## 7. Alignment with `~/.roxabi/lyra-nats-truth/`

| Topic | nats-truth direction | Autodeploy spec fit |
|---|---|---|
| M1 new programs (`lyra_cli`, `lyra_llm`) | `priority=300`, `startsecs=15` for CLI cold-start | Manifest adds entries; gate uses `state=RUNNING` with longer `timeout` |
| `lyra_harness` (M2) | Starts after `lyra_llm`, circuit breaker handles retry | Manifest declares `lyra_llm` before `lyra_harness`; no special code |
| Quadlet NATS | `Restart=always` already set | v2 adds `type: quadlet`; v1 leaves NATS untouched (it self-restarts) |
| Two hub instances (M3) | Zero-downtime deploy | v1 does sequential restart; M3 requires rolling strategy — out of scope now, spec has room |
| nkey provisioning (C1/C2 in nats-truth risks) | Pre-deploy hook needed | Manifest `pre_restart` covers this when time comes |

**Conclusion:** spec is forward-compatible with M0–M3. M3's rolling restart is the only item not yet modeled; a `strategy: rolling` field can be added to `services[*]` in schema v2 without breaking v1.

## 8. Resolved (was open) + remaining

**Resolved after architect + devops review (2026-04-13):**

1. ✅ **Language: Python** + `ruamel.yaml` (gate/rollback/trigger state machine is beyond bash's sweet spot; `uv` stack makes the venv free).
2. ✅ **`roxabi-ops` new repo** (future tenants: devops C2 SQLite backup, C10/C11 health probes, SSL renewal).
3. ✅ **YAML** (not all repos have `pyproject.toml` — roxabi-forge, roxabi-intel).
4. ✅ **Fail-isolated across repos by default**; triggers from failed repos are suppressed (§4.4).
5. ✅ **Always emit one heartbeat line per cycle** including `cycle=noop` (§4.8).

**Remaining (deferred — not blocking this spec):**

6. **Test gate: prod vs CI.** Today pytest runs on prod before restart. Devops notes this is a smell — a failing test on prod at 03:00 AM isn't actionable without CI context. Spec preserves current behavior; moving to CI is a **P4+ follow-on** requiring per-project secrets and different failure signaling.

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Runner bug stops all deploys | High | Keep old `deploy.sh` available through P3; fall back via disable-new-timer + enable-old-timer |
| Manifest schema churn | Medium | `schema:` field at top; runner rejects unknown versions with clear error |
| Python dep on prod (`ruamel.yaml`) | Low | Already using `uv` everywhere; install into `roxabi-ops` venv |
| Service name typo in manifest | Medium | Startup check: all `services[*].name` must exist in `supervisorctl avail` |
| Cross-repo trigger loops (A → B → A) | Low | Runner tracks visited repos per cycle; warns on cycle |

## 10. Acceptance criteria

- [ ] **AC1 — parity**: `autodeploy --dry-run` on prod produces a plan that matches current `deploy.sh` behavior byte-for-byte for lyra + voiceCLI (repos, steps, services, order).
- [ ] **AC2 — opt-in by manifest**: adding `deploy/auto-deploy.yml` with `schema: 1` + `branch: main` to `roxabi-forge` auto-pulls on next tick with zero runner-code edits.
- [ ] **AC3 — M16 closed**: simulated `git fetch` timeout (block origin) causes the repo's cycle to fail with a clear error; no stale-ref deploy.
- [ ] **AC4 — rollback on pre_restart fail**: `lyra_hub` test failure triggers `on_fail: rollback`; the **remote SHA** (`origin/<branch>` at time of failed pull, not pre-pull HEAD) lands in the blacklist; next cycle skips that SHA until a new one lands.
- [ ] **AC5 — fallback**: disabling `roxabi-autodeploy.timer` and re-enabling `lyra-deploy.timer` returns prod to old behavior with zero data loss.
- [ ] **AC6 — heartbeat**: a cycle where no repo changed emits **exactly one** `cycle=noop` line and exits 0.
- [ ] **AC7 — streaming logs**: no `capture_output=True` on any git / uv / pytest subprocess call; log file receives output in real time during a slow `uv sync` (> 5s).
- [ ] **AC8 — post-restart FATAL alarm**: if `lyra_hub` restarts but lands in `FATAL` / `BACKOFF`, `verify_all_services()` fails the cycle with non-zero exit and logs the failing service. No automatic rollback (rollback is pre-restart only).
- [ ] **AC9 — trigger dedup correctness**: voiceCLI update triggers lyra venv re-sync; `lyra_hub` restart fires **after** the re-sync even if it was already restarted earlier in the same cycle (venv changed).
- [ ] **AC10 — failed-repo trigger suppression**: if lyra's pre_restart fails, any trigger whose `in: ~/projects/lyra` is silently dropped that cycle.
- [ ] **AC11 — self-deploy abort**: a bad `roxabi-ops` commit whose `uv sync` fails does **not** rollback the ops venv; blacklist records the SHA; next cycle waits for a new commit.

## 11. Review asks

- **Architect**: does §4 (manifest + two-phase commit + service abstraction) hold up against nats-truth M0–M3? Is there a simpler factoring? Anything coupling to supervisord I missed?
- **DevOps**: is Python + `ruamel.yaml` the right call for prod, or should we stay in bash? Does §4.7 (explicit exit-status) fully fix M16? Is `roxabi-ops` the right home, or co-locate in an existing infra repo?
