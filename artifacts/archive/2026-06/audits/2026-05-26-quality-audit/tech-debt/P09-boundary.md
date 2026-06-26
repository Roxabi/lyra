# Tech Debt Audit — P09 Boundary Layer

**Date:** 2026-05-26
**Scope:** `src/lyra/integrations/**/*.py`, `src/lyra/tools/**/*.py`, `src/lyra/monitoring/**/*.py`, `src/lyra/obs/**/*.py`, `src/lyra/blobstore/**/*.py`
**Files:** 30 Python files
**Context:** Epic #1277 stage-axis refactor active; prior audit 2026-05-18 covered hexagonal conformance, mutualization, simplification — those findings are NOT re-reported unless regressed.

---

## Summary

- **Two deprecated modules** (`lyra.monitoring`, `SupervisorctlManager`) blocked on #1035 (Monitoring v2) with no visible concrete milestone since at least 2026-05-18 — this is the largest drain item in P09.
- **`obs/` scaffolding** (3 files, 0 runtime consumers) is intentional advance infra for #1235 (Langfuse), but adds to surface area with no integration timeline.
- **11 `DEBT:` annotations** are all boundary-broad-catch (6) or wiring-bootstrap-deps (3) — acceptable defensive patterns, not actionable regressions.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/monitoring/__init__.py` | 10 | MEDIUM | Package emits `DeprecationWarning` at import — superseded by Monitoring v2 (#1035). Module retained as "reference" with no removal date. | Set a concrete removal milestone in #1035 or spin off a dedicated cleanup issue with target date. |
| `src/lyra/integrations/supervisor.py` | 30 | MEDIUM | `SupervisorctlManager` raises `DeprecationWarning` on construction; retained until #1035 lands. Still imported by tests. | Remove once Monitoring v2 ships, or extract to `tests/_legacy/` if tests still need it after #1035. |
| `src/lyra/obs/` | module | LOW | 3 files, 0 runtime consumers in `src/lyra/`. Scaffolding for #1235 (Langfuse/OTel). `ObsCapabilities.async_flush` declared but no `flush()` method on Protocol. | Keep as intentional roadmap infra; add `flush()` to Protocol or remove `async_flush` from capabilities to eliminate known asymmetry. |
| `src/lyra/tools/gh_token/helper.py` | 11 | LOW | `TODO(T4)`: `MintError` should publish as `MintFailureEvent` on NATS (roxabi-contracts `gh/` schema). No milestone or issue linked. | File a tracking issue under #1035 or gh-token enhancement milestone; bound TODO to an issue number. |
| `src/lyra/monitoring/config.py` | 35 | LOW | Magic string `"claude-haiku-4-5-20251001"` hardcoded as `diagnostic_model` default. If model is retired, monitoring silently breaks. | Move to TOML default or env var fallback; add a config-level validation that model string is non-empty and well-formed. |
| `src/lyra/monitoring/checks.py` | 197 | LOW | Magic number `120` (reaper sweep age threshold in seconds) inlined in check logic. | Extract to `_REAPER_SWEEP_THRESHOLD_S` constant at module top, matching `_TIMEOUT_S` pattern in `systemctl.py`. |
| `src/lyra/integrations/base.py` | 24 | LOW | Default `timeout=30.0` repeated across `ScrapeProvider.scrape`, `VaultProvider.add`, `VaultProvider.search` with no named constant. | Extract `DEFAULT_INTEGRATION_TIMEOUT_S = 30.0` in `base.py` and reference from all implementations. |
| `src/lyra/integrations/audio.py` | 37 | LOW | `"48000"` sample rate literal passed to ffmpeg. | Document with a named constant `OPUS_SAMPLE_RATE = "48000"` or move to config if device-dependent. |

---

## Metrics

| Metric | Count |
|---|---|
| TODO / FIXME / HACK / XXX | 1 |
| Deprecated API usage (module-level) | 2 |
| `DEBT:` annotations | 11 |
| `boundary-broad-catch` | 6 |
| `wiring-bootstrap-deps` | 3 |
| Magic numbers / strings without named constants | 4 |
| ADR-048 migration items in P09 | 0 |
| Files in `file_exemptions.txt` (P09) | 0 |
| Folders in `folder_exemptions.txt` (P09) | 0 |

---

## Recommendations (prioritized)

1. **Bind #1035 to a concrete milestone** — `lyra.monitoring` and `SupervisorctlManager` are both gated on Monitoring v2. Without a removal date they accumulate invisible drag. Recommend: either scope #1035 to a specific epic slice with ETA, or open a standalone cleanup issue.

2. **Resolve `obs/` asymmetry** — `async_flush` bool without a `flush()` method on `ObservabilityProvider` is a small but real contract gap. Add the method to the Protocol (NoOp impl can be `pass`) or drop the capability flag until #1235 integration begins.

3. **Extract integration timeout constant** — `30.0` is duplicated in 5+ call sites across `base.py`, `vault_cli.py`, `web_intel.py`, `audio.py`. A single `DEFAULT_INTEGRATION_TIMEOUT_S` in `base.py` removes drift risk.

4. **Bound the gh_token T4 TODO** — `MintError` NATS event publishing is a reliability gap (silent mint failures in dispenser). File a tracking issue and link it in the TODO comment.

5. **Monitoring model string hygiene** — `claude-haiku-4-5-20251001` is a dated model identifier. If Anthropic retires this model, the monitoring escalation path degrades. Move default to TOML or env var to allow ops-side override without a code change.
