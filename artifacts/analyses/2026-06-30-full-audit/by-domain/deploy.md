# Domain audit: deploy (roxabi-factory + cluster deploy tooling)

Source: 7 findings (3 finders: `deploy-factory`, `deploy-cluster`, `deploy-acl-pipeline`) — REFUTED findings already excluded upstream. Verdicts/severities below are the post-verify-pass values (`verdict` + `adjusted_severity` where present; UNVERIFIED findings carry their original `severity` unchanged, not independently re-checked in this pass).

Note: an earlier draft of this file (different finding-ID set: `NO_RESTART=1`, `managed_repos` allowlist, etc.) previously occupied this path from a separate finder pass on the same domain. This write supersedes it with the 7-finding set supplied for this synthesis run.

## Dedup notes

- `deploy-cluster-quadlet-containers-role-blind` is flagged by its own evidence (`ssot_duplicate_of: lib/cluster_plan.py:roles_match`) as **related-but-distinct** from `deploy-cluster-role-rename-orphan`. Not merged: different file (`roxabi-factory/deploy/lib/quadlet-units.sh` vs `lib/cluster_plan.py`/`deploy.sh`) and different failure mechanism (client-restart fan-out list vs. unattended `--prune` deletion). Both stem from the same root cause — **no single canonical role-validation SSoT enforced across `hosts.toml`, every managed repo's `deploy/quadlet.toml`, and factory's own ad-hoc shell helpers** — and should be fixed together. Kept as two rows, cross-referenced.
- `deploy-factory-01` (Makefile `quadlet-install` blindly re-copies the static unit fleet `deploy.sh` already installed) and `deploy-cluster-quadlet-containers-role-blind` (factory's `quadlet-units.sh` re-enumerates units independently of `cluster_plan.py`) share a weaker family resemblance — both are cases of roxabi-factory's local deploy tooling duplicating logic that `~/projects/deploy.sh` / `cluster_plan.py` already own as SSoT — but touch different files/recipes and have different blast radii (install-time copy churn vs. restart-list enumeration). Not merged; noted for cross-domain awareness since the underlying anti-pattern (factory-local shell re-deriving cluster-level state) recurs.
- No other exact file:line overlaps among the 7 findings; each addresses a distinct mechanism (Makefile install duplication, missing `RestartForceExitStatus=`, langfuse `RestartSec` drift, role-rename orphan/prune, role-blind restart list, ACL grant gap ×ADAPTER, ACL fixture pre-push enforcement gap).

## Ranking (adjusted_severity desc, ties broken by verdict: CONFIRMED > PLAUSIBLE > UNVERIFIED)

### P0 — Critical

| # | File:line | Verdict | Title |
|---|---|---|---|
| 1 | `lib/cluster_plan.py:45` (projects-meta) | CONFIRMED | hosts.toml role renames desync silently from per-repo `quadlet.toml` `host_roles`, causing unattended `--prune` to delete live production units |
| 2 | `deploy/nats/acl-matrix.json:94` | CONFIRMED | telegram-adapter / discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()` `kv.watch()` fallback — same bug that crash-looped the dashboard same-day, never backported |

### P1 — High

_(none — no finding retained high severity after verification)_

### P2 — Medium

| # | File:line | Verdict | Title |
|---|---|---|---|
| 3 | `deploy/quadlet` (all 22 `.container`/`.container.tmpl` units) | UNVERIFIED | S12's `RestartForceExitStatus=` (fatal-config exit-code carve-out) absent from every factory Quadlet unit, unlike voiceCLI/llmCLI siblings |
| 4 | `roxabi-factory/deploy/lib/quadlet-units.sh:12` | UNVERIFIED | converge.sh's client-restart unit list (`quadlet_containers`) bypasses `cluster_plan.py`'s host-role SSoT entirely |
| 5 | `.pre-commit-config.yaml:117` / `tests/scripts/fixtures/v3-pre-grant-group.json` | UNVERIFIED | Hand-mirrored ACL parity fixture has zero local pre-push enforcement — only the full CI pytest run catches a stale mirror |

### P3 — Low

| # | File:line | Verdict | Title |
|---|---|---|---|
| 6 | `Makefile:165` | CONFIRMED (severity downgraded high→low on verify) | `make quadlet-install` (converge step 4) blindly re-installs the entire static unit fleet, contradicting its documented division of labor with `deploy.sh` |
| 7 | `deploy/quadlet/factory-langfuse-web.container:22` | UNVERIFIED | `factory-langfuse-web`/`-worker` deviate from S12's `RestartSec=10` with no documented rationale |

---

## Detail

### 1. [P0] hosts.toml role-rename desync → unattended `--prune` deletes live units — `lib/cluster_plan.py:45-48`, `184-190`

- **Verdict:** CONFIRMED, critical (kept)
- **Evidence:** `roles_match()` (`cluster_plan.py:45-48`) is a pure set-intersection with no validation against any canonical role registry; `hosts.toml`'s role catalogue (`hosts.toml:33-38`) is explicitly labeled "informatif" (unenforced comment). A role-mismatched component is logged as `# SKIP` to stderr only (`cluster_plan.py:184-190`) — never counted in `errors`/`soft_errors`. `deploy.sh`'s orphan-prune (`deploy.sh:418-468`) only gates on `errors==0`, so a role-desynced-but-previously-installed unit is auto-`rm -f`'d on the very next unattended run. `roxabi-factory/deploy/converge.sh` runs `deploy.sh --prune` every ~5 minutes via `factory-quadlet-sync.timer`/`factory-post-autoupdate.timer`, with no preview step. This exact fault class already happened in production: commit `c35721b`/#1801 ("rename M1 role `lyra-hub`→`factory-hub`") describes the identical mechanism stalling the entire factory stack on M1 because `hosts.toml` (in the separate, manually-pulled `projects-meta` repo) drifted from `roxabi-factory`'s auto-synced `quadlet.toml`. No CI runs `cluster_plan.py self-test` for `projects-meta` (no `.github/` dir exists there), and the existing self-test only hardcodes today's known-good role names, so it would not catch a *new* rename.
- **Mitigating factor found on verify:** `roxabi-factory/deploy/converge.sh:17` (`require_host_role factory-hub`, added 2026-06-28 — *after* the #1801 incident) hard-exits before `deploy.sh --prune` runs if the *host's* role set doesn't contain `factory-hub`. This would block a repeat of the exact #1801 *host-level* rename scenario, but does nothing for (a) a single component's `host_roles` value drifting independently while the host's own role set stays correct, or (b) any host other than the one this one literal string covers. The general mechanism — role desync → silent SKIP → unattended prune of a live unit — remains fully live for every other case, and an independent precedent (llmCLI's xai/fw-forwarder orphaned the same way, fixed via llmCLI PR #113) confirms the narrower per-component path already bit production once.
- **Recommendation:** Add a `[roles]` table in `hosts.toml` as the single enumerated source of valid role strings (or a CI check diffing every managed repo's `quadlet.toml` `host_roles` against it), and wire `cluster_plan.py self-test` into real CI for `projects-meta` (currently none exists). Until then, treat an unmatched-but-previously-installed unit as a `soft_error` (blocking `--prune` for that file) instead of a silent orphan.

### 2. [P0] telegram/discord-adapter missing ACL grant for `wait_for_hub()` cold-boot fallback — `deploy/nats/acl-matrix.json:94-150`

- **Verdict:** CONFIRMED, critical (kept)
- **Evidence:** `wait_for_hub()` (`packages/roxabi-nats/src/roxabi_nats/readiness.py:165-219`) tries `kv.get('hub.ready')` first; on `KeyNotFoundError` it falls through, **uncaught**, to `kv.watch('hub.ready')` (`readiness.py:128`, called before the function's own `try:` at line 129; `wait_for_hub()` itself has no surrounding try/except either). Traced 1:1 through nats-py: `KeyValue.watch()` → `subscribe()` with no explicit stream → `find_stream_name_by_subject()` → publishes `$JS.API.STREAM.NAMES`, then `watcher._sub.consumer_info()` needs `$JS.API.CONSUMER.INFO.<stream>.*`. Both telegram-adapter (`acl-matrix.json:102-113`) and discord-adapter (`:131-142`) grant `CONSUMER.CREATE.*`, `STREAM.INFO`, `STREAM.MSG.GET` but **not** `STREAM.NAMES` or `CONSUMER.INFO.*`; web-adapter has both. Both adapters call `wait_for_hub()` as an unguarded "load-bearing barrier" (`standalone_telegram.py:111`, `standalone_discord.py:143`) with no exception handling anywhere up to `factory/cli/main.py`'s bare `asyncio.run()`, so any timeout/denial crashes the process outright. **Proof this is live, not theoretical:** commit `95b566bc`, same day (2026-06-30), re-added exactly these two grants to web-adapter only, with message "Dashboard crash-looped on `$JS.API.STREAM.NAMES` and `CONSUMER.INFO` when `wait_for_hub` fell through to `kv.watch()`. Restores grants trimmed in #1727 while readiness.py still uses the watch path." telegram-adapter/discord-adapter — the two primary user-facing bot adapters, running the identical code path — were never backported. All `After=factory-hub.service` orderings (no `Requires=`) leave the cold-boot race window open on every restart.
- **Recommendation:** Add `$JS.API.STREAM.NAMES` and `$JS.API.CONSUMER.INFO.KV_factory-state.*` to telegram-adapter and discord-adapter's publish lists, regenerate the 4 derived ACL artifacts, hand-mirror the diff into `tests/scripts/fixtures/v3-pre-grant-group.json`. Longer-term: wrap the `kv.watch()` call itself (not just the iteration loop) in `readiness.py`'s try/except so a permissions violation degrades gracefully instead of crash-looping, and add the nats-server-backed integration test deferred to #716.

### 3. [P2] `RestartForceExitStatus=` (S12 fatal-config carve-out) absent from all 22 factory Quadlet units — `deploy/quadlet`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity)
- **Evidence (as submitted):** `container-deployment-standard.md:39` (S12) documents `RestartForceExitStatus=` for exit codes 100-127 (fatal config, don't restart-loop). `grep -rn RestartForceExitStatus deploy/quadlet/*.container*` returns zero matches across all 22 factory units. Sibling repos already implement this: `voiceCLI/deploy/quadlet/voicecli-stt.container:44` (`RestartForceExitStatus=3 78`), `llmCLI/deploy/quadlet/llmcli.container:44` (`RestartForceExitStatus=1 127`). `deploy/AGENTS.md` calls factory "the reference implementation for the Roxabi Quadlet pattern," yet it is the one repo missing this half of its own documented standard.
- **Recommendation:** Identify each container's fatal vs. transient exit codes (mirroring voiceCLI/llmCLI) and add `RestartForceExitStatus=` to every `factory-*.container` `[Service]` block, with a one-line comment documenting the chosen codes.

### 4. [P2] `quadlet_containers()` client-restart list bypasses `cluster_plan.py`'s role SSoT — `roxabi-factory/deploy/lib/quadlet-units.sh:12-20`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity)
- **Evidence (as submitted):** `quadlet_containers()` parses every `container = "X.container"` entry out of `deploy/quadlet.toml` with a plain grep and applies no `host_roles` filtering — a second, independent enumeration of "units that exist" alongside `cluster_plan.py`'s role-aware `roles_match()`/`scan_manifests()`. `converge.sh:117` (`mapfile -t _all_svcs < <(quadlet_containers)`) feeds this unscoped list straight into the structural-drift client-restart fan-out (`converge.sh:126-129`). Consistent today only because every `[component.*]` in `roxabi-factory/deploy/quadlet.toml` declares the identical `host_roles=["factory-hub"]` (verified across all 17 component blocks) — nothing enforces that invariant going forward.
- **Recommendation:** Derive the restart list from `cluster_plan.py`'s role-aware install-plan (single SSoT) instead of re-deriving it, or add an explicit assertion documenting the "all factory components share `host_roles=[factory-hub]`" invariant this script silently depends on. Cross-ref finding #1 (same root cause: no canonical role SSoT enforcement).

### 5. [P2] ACL parity fixture has zero local pre-push enforcement — `.pre-commit-config.yaml:117`, `tests/scripts/fixtures/v3-pre-grant-group.json`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity — finder's own notes already downgrade the worst-case framing: CI gates merge, so this is a dev-friction cost, not a deploy-correctness/security risk)
- **Evidence (as submitted):** `deploy/nats/acl-matrix.json` is documented SSoT driving 4 regenerated artifacts; 3 of them (706-spec, `v3-current.json`, `auth.conf`, `CURRENT.generated.md`) are covered by pre-push gates (`acl_specs_drift`, `acl_authconf_drift`, `architecture_snapshot` via `scripts/qg run --stage pre-push`). None of those gates invoke pytest or reference `v3-pre-grant-group.json`. The sole check comparing `acl-matrix.json` against this hand-maintained fixture is `tests/scripts/test_renderer.py::TestGrantGroupEquality::test_v4_render_set_equals_v3_render`, which runs only as part of the full CI `pytest tests/` invocation. `make hooks-install` wires `pre-commit install --hook-type pre-push` only; `make test` is a separate, never-auto-wired target; no single "regen everything" Makefile target covers all 4 artifacts + the fixture.
- **Recommendation:** Add a pre-push hook running `pytest tests/scripts/test_renderer.py -k TestGrantGroupEquality` so the hand-mirror requirement is enforced locally before push, not only in CI. Document the hand-mirror step in `CONTRIBUTING.md`/the ACL-change runbook.

### 6. [P3] `make quadlet-install` blindly re-installs the static unit fleet `deploy.sh` already installed — `Makefile:165-191`

- **Verdict:** CONFIRMED, **severity downgraded high→low on independent verify**
- **Evidence:** `Makefile:168`'s `rm -f` lists the same glob `"$(QUADLET_DIR)"/factory*.{network,volume,container,pod}` twice (literal copy-paste bug), and `Makefile:170-172` unconditionally re-`cp`'s every static unit with **no `cmp -s` idempotency gate**, unlike `~/projects/deploy.sh`'s INSTALL (`deploy.sh:229`) and AUXCOPY (`deploy.sh:360`) actions, which both skip the copy when content is unchanged. `deploy/converge.sh` step 3 (`deploy.sh --prune`, the new role-aware cluster SSoT) already installs that exact same static-unit fleet one step before `make quadlet-install` (step 4) redundantly re-touches it. Git history shows this is leftover from a 2026-06-28 migration (commit `229238f0` added the `deploy.sh --prune` step and documented the new step3/step4 split in `deploy/AGENTS.md:129-130`) that never trimmed the Makefile recipe to match.
- **Why downgraded:** Today the duplication is byte-identical and harmless — wasted I/O/mtime churn only. The original "high" framing hinged on the S20 `bot_secrets_fragment` splice pattern being live elsewhere in the cluster and at risk of being silently overwritten by this recipe; on verify, that pattern has **zero live usage** anywhere across all 4 managed repos (a misread of the standards doc's only worked example, which was factory's own now-deleted implementation), and factory explicitly migrated away from fragment-splicing to a DB-driven `tools/render_quadlet.py` RENDER-action mechanism for the only components that ever carried bot secrets (telegram/discord), with `tools/check_quadlet_template_purity.sh` (wired into `make quadlet-lint`) now permanently banning splice blocks and `Secret=factory-bot-*` lines in tracked `.container.tmpl` files, including a regression test for this exact reintroduction. The component class this finding worried about is structurally immune today.
- **Recommendation:** Narrow the Makefile `quadlet-install` recipe to only render/copy the two templated units (telegram, discord) plus `factory bot init`, dropping the blanket rm+cp of every static unit (already `deploy.sh`'s job). If a from-scratch bootstrap path still needs the full-fleet copy, gate it with the same `cmp -s` check `deploy.sh` uses. Fix the duplicated glob clause on `Makefile:168`.

### 7. [P3] `factory-langfuse-{web,worker}` RestartSec deviation — `deploy/quadlet/factory-langfuse-web.container:22`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original low severity)
- **Evidence (as submitted):** `RestartSec=15` vs. S12's standard `RestartSec=10`, used correctly by all other 20 factory units including the 4 sibling langfuse dependency units (postgres/clickhouse/redis/minio). No comment, exemption note, or ADR carve-out — the documented ADR-092 hardening exemption for these units covers `NoNewPrivileges`/`ReadOnly`/`DropCapability` only, not `RestartSec`. Introduced in commit `1d3a0d05` and unchanged since.
- **Recommendation:** Either change `RestartSec` to 10 on both units to match S12, or add a one-line comment justifying 15s (e.g., DB-dependent slower startup) so the deviation reads as intentional rather than copy-paste drift.

---

## Debt subscore: 33/100

Driven by 2 independently-CONFIRMED critical findings — one with same-day production precedent (`acl-pipeline-1`: dashboard literally crash-looped on this exact code path, fix not backported to the two primary bot adapters) and one with a dated production-incident history plus a newly-widened blast radius via unattended `--prune` (`cluster-role-rename-orphan`, partially but not fully mitigated by a 2026-06-28 host-level gate) — plus 3 UNVERIFIED mediums (missing `RestartForceExitStatus=` fleet-wide, a second role-SSoT-bypassing restart-list enumeration, and an ACL-fixture pre-push gap) and 2 low findings (one confirmed-but-downgraded Makefile duplication bug, one unverified `RestartSec` cosmetic drift). The two P0s alone — both already-manifested-or-precedented production-availability/data-loss mechanisms on the cluster's deploy path — anchor this well below the domain midpoint.
