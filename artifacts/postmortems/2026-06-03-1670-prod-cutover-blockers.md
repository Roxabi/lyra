---
title: "#1670 production cutover — blockers encountered & root-cause classification"
date: 2026-06-03
issue: 1670
related: [1710, 1082, 1715]
status: CUTOVER COMPLETE 2026-06-03 14:21 CEST — factory-* live on M₁, lyra-* stopped+retained (rollback), in soak
author: Claude (driven by Mickael)
---

# #1670 `factory.*` wire cutover — blocker postmortem

Recap of every problem hit while attempting the live `lyra.*`→`factory.*` cutover on M₁
(`roxabituwer`), classified as **CODE** (a real defect/design flaw in the repo), **DEPLOY**
(deployment-state / operational gap, not a code bug), or **PROCESS** (my own execution error).

The merge itself (PR #1709) and all reversible prep succeeded; the live `lyra-*` deployment was
never disturbed. The blockers below are why the *production cutover* is not a simple flip.

---

## Meta root cause (ties ~70% of the blockers together)

> **The `lyra`→`factory` rename was landed piecemeal in the repo — CI image path, container
> user, data-dir default, secret names, ACL CLI name, Quadlet unit names, bot-seed schema, docs
> — but was NEVER executed as an atomic deployment migration on M₁.**

Result: the **code expects the `factory` end-state**, the **running deployment is the `lyra`
start-state**, and **no migration bridges them**. Most blockers are this single gap surfacing
per-tool (image path, secret names, data dir, repo path…). It is *not* a single bug — it is a
**release-sequencing decision** (rename-in-code-first, deploy-later) that left a long-lived,
internally-inconsistent partial-migration state with no automated bridge.

On top of that meta-gap sit **5 genuine, independent code defects** (#9–#13 below) that the
migration *exposed* — they would bite any future operator regardless of the rename.

---

## Blocker table

| # | Blocker | Class | Severity |
|---|---------|-------|----------|
| 1 | `docs/QUADLET-DEPLOYMENT.md` presents `factory-*` as the live deployment; M₁ runs `lyra-*` | DOC/DEPLOY | low (fixed: PR #1715) |
| 2 | M₁ repo `~/projects/lyra` was **265 commits behind** `staging` | DEPLOY | med |
| 3 | `factory-quadlet-sync.timer` (auto-converge) **not installed** on M₁ | DEPLOY | med |
| 4 | GHCR image repo renamed `lyra`→`factory` in CI; deployed units still pin `ghcr.io/roxabi/lyra:*` | CODE/RELEASE | high |
| 5 | new image `User=factory` + data dir `~/.roxabi/factory`; deployed units mount `lyra` paths | DEPLOY (by design) | high |
| 6 | `/data` absent + no passwordless sudo; blobstore canonical path is root-owned `/data/factory/blobs` | DEPLOY/DESIGN | med |
| 7 | `factory-acl genkeys` (full) **requires root**, but seeds are `mickael`-owned | CODE/DESIGN | med |
| 8 | gh-helper NATS identity (#1082) **never provisioned** on M₁ (no seed/secret) | DEPLOY/PROCESS | med |
| 9 | `install.sh` `SEEDS` dict **omits `gh-helper`** (Makefile's list includes it) | **CODE** | med |
| 10 | `factory-acl` resolved seeds to `~/.lyra/nkeys` despite `factory_data_dir()==~/.roxabi/factory` | **CODE (suspected)** | med |
| 11 | `config.toml` schema drift: `factory bot init` rejects `owner_users` ints + `default` key | **CODE** | med |
| 12 | `make quadlet-install` does `rm -f factory*` **then** aborts at `bot init` → half-deleted unit set | **CODE** | high |
| 13 | `install.sh` blob symlink-over-existing-dir left blobs misplaced | CODE (minor) | low |
| 14 | clipool crash-loop at cutover: `git_ownership_probe` failed — `~/projects/roxabi-factory` bridge symlink was **absolute** (`→ /home/mickael/projects/lyra`) → dangles inside container ns | DEPLOY/CODE | high (blocked clipool; fixed live) |
| — | podman `secret create` missing `-`; misread diff (wrong file); `ROXABI_FACTORY_DIR` export ineffective | PROCESS (me) | low |

---

## Genuine CODE defects (independent of the rename — should be filed/fixed)

### #9 — `install.sh` secret list drifts from the Makefile (and from the ACL SSoT)
- **Symptom:** after `install.sh`, `factory-nats-gh-helper` was absent → had to create it by hand.
- **Root cause:** the identity→secret mapping is **hardcoded in two places** — `deploy/install.sh`
  `SEEDS=(...)` (7 nats secrets, **no gh-helper**) and `Makefile` `nats-secrets-install`
  (lines 210-215, **8 secrets incl. `factory-nats-gh-helper`**). They drifted. Neither is derived
  from `deploy/nats/acl-matrix.json` (the declared SSoT).
- **Deep cause:** SSoT violation — the secret/identity set is duplicated as literals instead of
  generated from `acl-matrix.json`. Adding an identity (#1082 gh-helper) updated the matrix + the
  Makefile but not `install.sh`.
- **Fix:** derive the `SEEDS` set from `acl-matrix.json` in both `install.sh` and the Makefile (or
  have one call the other). Add a gate asserting `install.sh secrets == acl-matrix identities`.

### #11 — `_BotSeedEntry` breaking-validates existing `config.toml`
- **Symptom:** `factory bot init` → `validation error … string_type` on all 4 bots → `make
  quadlet-install` aborts.
- **Root cause:** `src/factory/agent_cmd/bots/init.py` `_BotSeedEntry` declares
  `owner_users: list[str]` + `model_config = ConfigDict(extra="forbid")`, but live `config.toml`
  carries `owner_users = [7377831990]` (**ints**) and a `default = "blocked"` key. The construction
  code *does* back-compat `default`→`default_trust` (line ~197), but the **validation model rejects
  the int `owner_users`** (and `extra="forbid"` would reject stale keys).
- **Deep cause:** a breaking type/key change to the seed schema shipped **without a config.toml
  migration or coercion** (e.g. accept `int|str` for ids, or migrate-on-read).
- **Severity note:** **non-fatal at runtime** — the hub reads `BotStore` (`config.db`), not
  `config.toml`, and the new code reads the existing `config.db` correctly (proven: adapters
  rendered from it). So this only breaks the *seed* path, not the running bots.
- **Fix:** coerce `owner_users` ints→str on load (or `list[int|str]`), and don't `forbid` legacy
  keys you already back-compat.

### #12 — `make quadlet-install` is destructive-then-fallible (non-atomic)
- **Symptom:** the failed `bot init` left the unit dir with `nats`/`hub`/volumes but **missing**
  `blobstore`/`clipool`/`gh-helper`/`turn-writer`/`gh.pod` — a half-installed deployment.
- **Root cause:** the target runs `rm -f "$QUADLET_DIR"/factory*` **first**, copies a few static
  units, then runs `uv run factory bot init` (fallible), and only *after* that copies the
  remaining units + renders adapters. A `bot init` failure (here: #11) aborts `make` mid-sequence,
  leaving units deleted-but-not-recopied.
- **Deep cause:** **no atomicity** — destructive cleanup precedes a fallible step with no
  staging/rollback. On a live host this can brick a deployment on any transient failure.
- **Fix:** render into a temp dir and swap atomically; or move `bot init` before the `rm`; or make
  `bot init` non-fatal (warn + continue when `BotStore` is already populated).

### #7 — `genkeys` root-guard vs rootless seed ownership (inconsistent)
- **Symptom:** `factory-acl genkeys` (full) → `error: must be run as root`, but `--add-identity`
  and `--regen-authconf` run rootless, and seeds are `mickael:mickael 0600`.
- **Root cause:** a legacy "nkeys are root-managed" guard on the full-`genkeys` path conflicts with
  (a) the rootless `--add-identity` path that *does* mint seeds, and (b) rootless seed ownership.
  Running it as root would actually **mis-own** the seeds.
- **Deep cause:** the root-guard predates the rootless (`systemd --user`) deployment model and was
  never reconciled.
- **Fix:** drop/relax the root guard for the rootless seed dir, or document `--add-identity` as the
  sanctioned single-identity path (it worked here).

### #10 — split-brain data-dir resolution (suspected code)
- **Symptom:** `factory-acl` read/wrote `~/.lyra/nkeys` while `factory_data_dir()` returned
  `~/.roxabi/factory` (verified via `python -c`), and an explicit `ROXABI_FACTORY_DIR` export was
  ignored.
- **Root cause (NOT fully traced):** `cli_ops.py` sets `_DEFAULT_SEEDS_DIR =
  factory_data_dir()/"nkeys"` → should be `~/.roxabi/factory/nkeys`, yet `genkeys` used `~/.lyra`.
  Indicates a **second, divergent resolution** (a different default, an `acl-matrix.json`-embedded
  path, or a host env/Makefile `FACTORY_NKEYS_DIR`) that disagrees with `factory_data_dir()`.
- **Deep cause:** more than one source of truth for "where the nkeys live."
- **Action:** trace the genkeys seeds-dir resolution; unify on `factory_data_dir()`; honor
  `ROXABI_FACTORY_DIR` everywhere.

### #13 — `install.sh` blob symlink over an existing dir (minor)
- `install.sh` reports `~/.roxabi/factory/blobstore → /data/factory/blobs` but didn't replace the
  pre-existing directory; blob placement had to be done manually. Blob-path setup assumes a clean
  state. Low severity.

### #14 — absolute bridge-symlink dangles across the container namespace (live cutover)
- **Symptom:** at cutover, `factory-clipool` crash-looped (3× restart) with
  `git_ownership_probe: target directory does not exist (target=/home/factory/projects/roxabi-factory)`.
  All other 8 services were green.
- **Root cause:** the host carries the repo at `~/projects/lyra` (Phase-2 infra rename not done) with
  a bridge symlink `~/projects/roxabi-factory → /home/mickael/projects/lyra` (**absolute**). clipool
  bind-mounts `%h/projects:/home/factory/projects`, so inside the container the symlink resolves to
  `/home/mickael/projects/lyra` — a path that **does not exist in the container mount namespace** (the
  repo is mounted at `/home/factory/projects/lyra`). The new `git_ownership_probe` (factory bootstrap)
  hard-requires `…/roxabi-factory` to exist → fails closed → exit 1.
- **Deep cause:** two compounding facts — (a) the bridge symlink is itself a **symptom of the meta
  root cause** (host still on `lyra` naming, code expects `roxabi-factory`); (b) an absolute symlink
  is namespace-fragile — only a **relative** symlink (`roxabi-factory → lyra`) resolves correctly in
  *both* the host and the bind-mounted container namespace.
- **Fix (applied live):** `ln -sfn lyra ~/projects/roxabi-factory` → clipool probe `OK`, NRestarts=0.
- **Follow-up:** either (a) finish Phase-2 (rename the real dir `~/projects/lyra` → `roxabi-factory`,
  drop the symlink), or (b) codify "cross-namespace bridge symlinks MUST be relative" in the deploy
  standard, or (c) make `git_ownership_probe` resolve/realpath leniently and emit a clear remediation
  hint instead of a bare crash. Fold into #1710.

---

## NOT code-caused (deployment-state / operational)

- **#4 image-repo rename (CODE/RELEASE boundary):** `publish.yml` now pushes
  `ghcr.io/roxabi/factory:{staging,staging-svc}`, but the deployed Quadlet units pin
  `ghcr.io/roxabi/lyra:*` — a now-**frozen** path CI no longer updates. The image rename got *ahead*
  of the consumers. *Root cause = release coordination*: rename the published artifact and its
  consumers in lockstep, or dual-tag during transition. (Borderline code: the CI change is in-repo.)
- **#1 docs:** forward-dated runbook, no "not-yet-deployed" caveat. Fixed in **PR #1715**.
  The `doc_drift` gate can't catch it (it compares code↔doc *in-repo*, never repo↔deployed host).
- **#2 / #3 stale repo + no sync timer:** M₁'s `~/projects/lyra` is manually managed and was 265
  commits behind; the documented `factory-quadlet-sync.timer` was never installed. Operational.
- **#5 image user/data-dir:** `User=lyra→factory`, `~/.lyra→~/.roxabi/factory` — the intended
  Phase-2 infra rename, simply **not deployed**. Working as designed; no bridge run yet.
- **#6 `/data` + sudo:** the new blobstore's canonical `/data/factory/blobs` needs root
  provisioning the rootless `install.sh` can't do (we routed to `~/.roxabi/factory/blobstore`
  instead). Design-ergonomics, not a bug.
- **#8 gh-helper not provisioned:** #1082 added the identity to `acl-matrix.json` but its M₁
  provisioning (seed+secret) was never run. Deploy/process gap (compounded by #9).

## PROCESS (my execution errors — for honesty)

- `podman secret create --replace <name>` missing the `-` stdin-source arg (fixed).
- Reported a "✓ key-preserving" diff against the **wrong file** while a regen had silently aborted
  (caught on re-check; corrected).
- Assumed `export ROXABI_FACTORY_DIR` would steer `factory-acl` — it didn't (led to discovering #10).

---

## Recommendations (follow-up issues to file)

1. **CODE** — derive secret/identity lists from `acl-matrix.json` in `install.sh` + Makefile; add a
   drift gate (#9).
2. **CODE** — make `_BotSeedEntry` tolerate legacy `config.toml` (coerce `owner_users` ints, don't
   `forbid` back-compat keys) **or** ship a `config.toml` migration (#11).
3. **CODE** — make `make quadlet-install` atomic (stage+swap) or move `bot init` before the `rm`
   (#12).
4. **CODE** — reconcile the `genkeys` root-guard with the rootless model (#7) and unify data-dir
   resolution on `factory_data_dir()` / `ROXABI_FACTORY_DIR` (#10).
5. **RELEASE** — treat the `lyra→factory` infra rename as **one atomic, scripted migration**
   (image-repo re-point + data move + secret rename + unit swap + auth regen), not a piecemeal
   code-first drift. Fold into #1710. Until then, keep dual GHCR tags so deploys don't break (#4).
6. **OPS** — install `factory-quadlet-sync.timer` (or document M₁ as manually-synced) and keep the
   M₁ repo current (#2/#3).
7. **DEPLOY** — codify "cross-namespace bridge symlinks MUST be relative"; prefer completing the dir
   rename + a back-compat symlink over absolute bridges (#14, R3 below).
8. **RELEASE** — automate satellite lockstep: a re-pin should be `uv lock --upgrade` + rebuild +
   redeploy, fully scripted per satellite; forbid hardcoded wire-subject literals in satellites
   (they must import from `roxabi_contracts`) — add a gate (R4 below). Add `pytest-timeout` to every
   satellite so a leaked real-resource test fails fast instead of hanging CI for an hour (R7 below).
9. **OPS** — add a repo↔deployed-host config-drift check (quadlet units, env files) so uncommitted
   edits like the M₂ mem-caps (R5 below) surface before a hard reset eats them.

---

## Resolution — full sequence (2026-06-03)

The cutover and all follow-on cleanup completed the same day. Final state: **`factory.*` is sole
prod, zero `lyra.*` on the wire, 0 ACL violations system-wide, voice dictate confirmed working, and
all three satellites (llmCLI, voiceCLI, imageCLI) re-pinned + merged (issues #105/#189/#104 closed).**

### Timeline

| Time (CEST) | Step | Outcome |
|------|------|---------|
| 14:21 | **Cutover** — stop `lyra-*` → start `factory-*` | 9 units green, `FACTORY_*` streams + KV created, 4 bots live, 0 `factory.*` violations |
| 14:23 | **#14 fix** — clipool crash-loop | relative symlink → probe OK (later superseded by real rename) |
| 14:48 | **Repo dir rename** `~/projects/lyra` → `~/projects/roxabi-factory` (the real Phase-2 fix; hardcoded `DEFAULT_PROBE_PATH` now matches reality) | clipool probe OK vs real dir; 9/9 green |
| 14:50 | **Decommission** — lyra-* units → backup dir, lyra images deleted | 0 lyra unit-files (reboot-safe) |
| 14:52 | **M₂ llm-worker stopped** (broken, spamming `lyra.llm.*`) | ACL spam halted; cloud LiteLLM covers hub |
| ~15–16 | **Satellite re-pin** — llmCLI PR #107, voiceCLI PR #190 | contracts 0.4.0/0.6.0 → 0.7.0; images rebuilt; workers redeployed; **0 violations** |
| 16:23 | **Purge** — `~/.lyra` (archived 48M), 14 lyra-* secrets, `image prune -a`, remote → `roxabi-factory.git`, worktrees pruned | factory-* green; lyra fully gone |
| — | **llmCLI quadlet mem-caps** committed (PR #108) | repo↔host drift closed |
| ~17–18 | **Satellite re-pin (imageCLI)** — PR #107 (issue #104) | contracts 0.6.0→0.7.0, URLs→roxabi-factory.git; worker DORMANT (image rebuilt, no redeploy); CI exposed a latent test hang → fixed in-PR (R7) |

### New findings surfaced during cleanup (beyond the cutover blocker table)

- **R3 — rename blast-radius (absolute symlinks).** Renaming the real dir dangled *other* absolute
  symlinks beyond the bridge: `~/.roxabi/factory/config.toml` and `~/.lyra/config.toml` (both →
  `…/projects/lyra/config.toml`, repointed), plus **16 roxabi-forge brand symlinks**
  (`~/roxabi-sync/forge/{,_dist/}lyra/brand/*`). The forge ones were verified **pre-broken**
  (the repo has no `brand/` dir → they dangled before the rename; roxabi-forge's cleanup, not ours).
  Resolution: a **back-compat symlink** `~/projects/lyra → roxabi-factory` makes `projects/lyra/X ≡
  roxabi-factory/X`, so all legacy absolute refs keep resolving without churning the Syncthing share.
  Lesson: renaming a dir with unknown external absolute refs needs either a back-compat alias or a
  full audit + repoint; absolute symlinks are the hidden cost of a half-done rename (meta root cause).

- **R4 — satellite hardcoded wire literals defeat contracts-as-SSoT.** `roxabi-contracts` is the wire
  SSoT, yet **llmCLI hardcoded** `lyra.llm.lifecycle.*` string literals in `nats/_lifecycle.py` —
  so a dep bump alone would NOT have fixed the lifecycle subjects (the fix imported them from
  `roxabi_contracts.llm` instead). **voiceCLI was clean** (every subject from contracts) → pure
  re-pin. The asymmetry is the lesson: forbid raw wire-subject literals in satellites.
  Also: both satellites pinned `Roxabi/lyra.git` (worked only via GitHub's rename redirect) with the
  uv lock **frozen at an old commit** despite `branch=staging` — "floating" pins silently go stale
  until `uv lock --upgrade`. Both URLs updated to `roxabi-factory.git`.

- **R5 — repo↔host config drift (M₂ quadlets).** `deploy/quadlet/llmcli-*.container` on M₂ carried
  uncommitted `MemoryHigh/MemoryMax` caps (added 2026-06-01) — deployed but never committed → would
  vanish on a hard reset. Same drift class as #2/#3 (host ahead of repo, here). Committed via PR #108.

- **R6 — `image prune -a` ≠ free lunch.** Removing lyra images + `prune -a` freed ~5GB total, but
  ~11.5GB of "reclaimable" remained: it is held by **other M₁ projects' images** (voiceCLI / hermes /
  imageCLI), not lyra. Reported honestly; not force-removed. "Reclaimable" is host-wide, not per-app.

- **R7 — satellite re-pin CI exposed a latent test-isolation bug (imageCLI).** imageCLI #107's CI
  hung ~56 min on `tests/nats/test_integration.py::test_adapter_handles_generation_failure`. A
  faulthandler dump pinned it: the test patched `get_engine` + `preflight_check` but **not**
  `model_registry.model_registry.get`, and `ImageNatsAdapter.handle()`'s no-LoRA path resolves the
  engine via `model_registry.get()` — so the **real Flux2 diffusion pipeline** loaded and ran a
  50-step inference (CI: multi-GB HF model download; cached host: ~1h). Pre-existing latent bug
  (same class as imageCLI #103), unrelated to the wire rename — the re-pin's CI was simply the gate
  that surfaced it (deps shifted just enough to flip the real engine from fast-fail to actually
  loading). Fix: add the missing mock (1 line, sibling pattern) → full suite **302 passed / 70s**.
  Lessons: (a) treat each satellite's CI as a real regression gate, not a rubber stamp; (b) add
  `pytest-timeout` to satellites so a leaked real-resource test **fails fast** instead of hanging the
  runner for an hour; (c) faulthandler (`-o faulthandler_timeout=N`) turns an opaque CI hang into a
  precise stack in one run.

### Recoverable artifacts (kept post-purge)

- `~/.lyra-archive-20260603.tar.gz` (M₁, 48M) — full `~/.lyra` incl. the diverged `turns.db`
  (the only copy of May-25→Jun-3 turn history). Delete for zero-trace.
- `~/.config/containers/systemd.lyra-decomm-20260603-145026` (M₁) — lyra-* Quadlet unit backup.
- Back-compat symlink `~/projects/lyra → roxabi-factory` (M₁).

### Sole remaining loose end

Orphan **empty** streams on the factory jetstream volume (`LYRA_*`/`lyra-*`/`KV_lyra-*`, 0 messages —
leftover defs from when `~/.roxabi/factory` was first populated ~May 25; **not** the live data, which
was on the separate `~/.lyra` volume now purged). Deletion deferred: no `nats` CLI in the factory
images; needs an admin nats client (e.g. a `nats-box` one-shot). Harmless — pure `nats stream ls`
clutter.
