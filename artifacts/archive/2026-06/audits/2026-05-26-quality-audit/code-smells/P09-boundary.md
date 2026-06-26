# P09 Boundary Layer Code-Smell Audit

**Scope:** integrations, tools, monitoring, obs, blobstore
**Date:** 2026-05-26
**Prior audit:** 2026-05-18 (hexagonal/duplication/dead-code) — regressions noted where observed.

---

### Summary

- P09 is structurally healthy: zero files exceed 300 lines, zero functions exceed 100 lines, and the 8 functions >50 lines are all boundary-glue (subprocess wrappers, health-check orchestration) rather than business logic.
- The dominant smell is a **copy-paste subprocess pattern** repeated in 5 integration files (plus 2 extra in vault_cli search) — ~15 lines of timeout/kill/returncode/FileNotFoundError logic duplicated verbatim with only exception types changed.
- `handle_delete` in `_handlers.py` is the single highest-complexity site due to dual key-resolution paths, a pre-delete existence check, and 4 nested exception blocks.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/integrations/supervisor.py` | 68–89 | **Medium** | Subprocess orchestration pattern duplicated verbatim from `systemctl.py` / `audio.py` / `web_intel.py` / `vault_cli.py` | Extract `run_subprocess` helper in `integrations/base.py` |
| `src/lyra/integrations/systemctl.py` | 72–101 | **Medium** | Same subprocess pattern as above; additionally `control` duplicates status-vs-action branching logic from `supervisor.py` | Consolidate via shared helper + strategy dict |
| `src/lyra/integrations/audio.py` | 30–58 | **Medium** | Same subprocess pattern; `convert_wav_to_ogg` is structurally identical to `web_intel.py::scrape` and `vault_cli.py::add` | Use extracted helper; file can shrink to ~25 lines |
| `src/lyra/integrations/web_intel.py` | 44–79 | **Medium** | Same subprocess pattern; also JSON parse + success-check logic is a second duplicated mini-pattern | Use helper for subprocess; consider `json.loads` guard utility |
| `src/lyra/integrations/vault_cli.py` | 78–93, 98–111 | **Medium** | **Two** subprocess copies in one file (`add` + `search`); `search` additionally swallows all errors (by design) diverging only at the tail | Extract helper with `raise_on_error: bool` flag; `search` can call it with `raise_on_error=False` |
| `src/lyra/tools/gh_token/helper.py` | 187–255 | **Low** | `mint()` is 69 lines — HTTP headers, request, 3 exception types, JSON parse, tz fix — all inline | Extract `_post_mint` or `_parse_token_response` to cut `mint` to ~40 lines |
| `src/lyra/tools/gh_token/helper.py` | 1–255 | **Low** | File hosts 5 responsibilities: `InstallationToken`, `MintError`, `JWTSigner`, `TokenCache`, `mint` — god-module smell | Split into `gh_token/models.py`, `gh_token/signer.py`, `gh_token/cache.py` |
| `src/lyra/blobstore/_handlers.py` | 223–297 | **Medium** | `handle_delete` is 75 lines: int-regex vs SQL JOIN resolution, pre-check SELECT, then actual delete, 4 exception blocks | Extract `_resolve_blob_ref_id(key, store)` and `_pre_check_exists(conn, ref_id)` helpers |
| `src/lyra/blobstore/_handlers.py` | 75–82 | **Low** | `_conn(store)` reaches into `FsBlobStore._conn` (private attr) — feature envy / encapsulation leak | Add `store.ensure_open()` or expose public `connection` property on `FsBlobStore` |
| `src/lyra/blobstore/_handlers.py` | 35–67 | **Low** | `_emit_audit` duplicated conceptually in `auth.py` (lines 37–56); both build `BlobAuditEvent` inline with slightly different defaults | Move `_emit_audit` to shared module or add `BlobAuditEvent.for_http_request(...)` factory |
| `src/lyra/blobstore/serve.py` | 132–161 | **Low** | `build_app` mixes auth middleware wiring, lifespan injection, route registration, and state init — 4 responsibilities | Extract `_init_app_state(app, token, nats)` and `_attach_routes(app)` private helpers |
| `src/lyra/monitoring/checks.py` | 206–286 | **Low** | `run_checks` is 81 lines of sequential check orchestration with conditional enablement gates — linear but long | No refactor needed until an 11th check lands; current structure is a numbered pipeline |
| `src/lyra/monitoring/checks_varz.py` | 32–105 | **Low** | `check_nats_varz` is 74 lines: state load, HTTP fetch, delta calc, state persist, failure formatting | Extract `_load_state` / `_persist_state` / `_delta_check` helpers |
| `src/lyra/monitoring/escalation.py` | 30–44 | **Low** | `_build_user_message` builds identical `{"name", "detail"}` dicts for passed and failed checks — 2 near-identical list comprehensions | Single comprehension with filter param, or inline in f-string |
| `src/lyra/monitoring/config.py` | 82–86 | **Low** | `_validate_quiet_times` model_validator is a no-op placeholder — misleading; validator with zero logic adds cognitive overhead | Remove until cross-field validation is actually needed, or add real logic |
| `src/lyra/monitoring/__main__.py` | 24–84 | **Low** | `_run` is 61 lines with 3 nested try/except blocks for LLM → raw-Telegram → diagnosed-Telegram fallback | Extract `_try_layer2(report, config)` helper to flatten `_run` |
| `src/lyra/integrations/supervisor.py` | 1–91 | **Info** | Module is deprecated since #611/#886, retained only for test suite + #1035 — dead code by intent, but still shipped | Track #1035 removal; do NOT refactor (would waste effort) |

---

### Metrics

| Metric | Value |
|---|---|
| Files analyzed | 28 |
| Total lines | 3,114 |
| Files >300 lines | 0 |
| Functions >50 lines | 8 |
| Functions >100 lines | 0 |
| Classes >3 methods | 4 |
| DRY violations (subprocess pattern, ≥3 lines, ≥2 files) | 5 files, 7 occurrences |
| God modules (>5 responsibilities) | 2 (`helper.py`, `_handlers.py` counting helpers) |
| Deprecated / dead-by-intent files | 1 (`supervisor.py`) |
| 300-line exemptions needed | 0 |
| Exemptions violated | 0 |

**Cognitive complexity hotspots (estimated):**
- `handle_delete` ≈ 18 (nested branches: regex match → SQL JOIN → pre-check → delete → 4 except blocks)
- `check_nats_varz` ≈ 14 (state load + HTTP + delta + persist + branching failures)
- `run_checks` ≈ 12 (linear but 10 sequential checks with 3 conditional gates)
- All other functions <12

---

### Recommendations (prioritized)

1. **Extract subprocess runner helper in `integrations/`** — Highest ROI. The 15-line `create_subprocess_exec → wait_for → TimeoutError(kill/wait) → returncode check → FileNotFoundError` pattern is copy-pasted in 5 files (7 call sites). A single `async def _run_cmd(cmd, timeout, *, on_timeout, on_error, on_missing)` in `integrations/base.py` removes ~90 lines of duplication and makes the integration files read as declarative intent rather than plumbing.

2. **Split `gh_token/helper.py` into 3 files** — `helper.py` hosts `InstallationToken`, `MintError`, `JWTSigner`, `TokenCache`, and `mint()` — 5 responsibilities in 255 lines. Split to `models.py` (token + errors), `signer.py` (JWTSigner + `_b64url`), `cache.py` (TokenCache + `_parse_expires_at`), leaving `mint` in `helper.py` or moving it to `dispenser.py` where it is consumed. Each file would be <100 lines and testable in isolation.

3. **Extract `_resolve_blob_ref_id` and `_pre_check_exists` from `handle_delete`** — `handle_delete` is the most complex function in P09 (75 lines, estimated cognitive complexity ~18). Extracting the dual key-resolution strategy and the existence pre-check into small, named helpers would drop the function to ~30 lines and make the TOCTOU comment readable at a glance.

4. **Move `_emit_audit` or add `BlobAuditEvent` factory** — `auth.py` and `_handlers.py` both construct `BlobAuditEvent` inline with 8+ keyword args. The `_emit_audit` helper exists only in `_handlers.py`; `auth.py` re-implements a narrower version. A shared factory (`BlobAuditEvent.from_request(...)` or a module helper) removes the duplication and reduces drift risk when the event schema changes.

5. **Flatten `_run` in `monitoring/__main__.py`** — The 61-line `_run` has 3 nested try/except blocks for LLM → raw Telegram → diagnosed Telegram fallback. Extracting a `_try_layer2(report, config)` helper that returns `(diagnosis | None, error)` would collapse `_run` to ~30 lines and make the fallback pipeline explicit rather than nested.
